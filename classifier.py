# YOLOv5 🚀 by Ultralytics, GPL-3.0 license
"""
Train a YOLOv5 classifier model on a classification dataset

Usage-train:
    $ python path/to/classifier.py --model yolov5s --data mnist --epochs 5 --img 128

Usage-inference:
    import cv2
    import numpy as np
    import torch
    import torch.nn.functional as F

    # Functions
    resize = torch.nn.Upsample(size=(128, 128), mode='bilinear', align_corners=False)
    normalize = lambda x, mean=0.5, std=0.25: (x - mean) / std

    # Model
    model = torch.load('runs/train/exp2/weights/best.pt')['model'].cpu().float()

    # Image
    im = cv2.imread('../mnist/test/0/10.png')[::-1]  # HWC, BGR to RGB
    im = np.ascontiguousarray(np.asarray(im).transpose((2, 0, 1)))  # HWC to CHW
    im = torch.tensor(im).unsqueeze(0) / 255.0  # to Tensor, to BCWH, rescale
    im = resize(normalize(im))

    # Inference
    results = model(im)
    p = F.softmax(results, dim=1)  # probabilities
"""

import argparse
import logging
import math
import os
from copy import deepcopy
from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim
import torch.optim.lr_scheduler as lr_scheduler
import torchvision
import torchvision.transforms as T
from torch.cuda import amp
from tqdm import tqdm

from models.common import Classify
from utils.general import set_logging, check_file, increment_path, check_git_status, check_requirements
from utils.torch_utils import model_info, select_device, is_parallel

# Settings
logger = logging.getLogger(__name__)
set_logging()


# Show images
def imshow(img):
    import matplotlib.pyplot as plt
    import numpy as np

    plt.imshow(np.transpose((img / 2 + 0.5).numpy(), (1, 2, 0)))  # unnormalize
    plt.savefig('images.jpg')


def train():
    save_dir, data, bs, epochs, nw, imgsz = Path(opt.save_dir), opt.data, opt.batch_size, opt.epochs, \
                                            min(os.cpu_count(), opt.workers), opt.img_size

    # Directories
    wdir = save_dir / 'weights'
    wdir.mkdir(parents=True, exist_ok=True)  # make dir
    last, best = wdir / 'last.pt', wdir / 'best.pt'

    # Download Dataset
    if not Path(f'../{data}').is_dir():
        url, f = f'https://github.com/ultralytics/yolov5/releases/download/v1.0/{data}.zip', 'tmp.zip'
        print(f'Downloading {url}...')
        torch.hub.download_url_to_file(url, f)
        os.system(f'unzip -q {f} -d ../ && rm {f}')  # unzip

    # Transforms
    trainform = T.Compose([
                           T.RandomGrayscale(p=0.01),
                           T.RandomHorizontalFlip(p=0.5),
                           T.RandomAffine(degrees=1, translate=(.2, .2), scale=(1 / 1.5, 1.5),
                                          shear=(-1, 1, -1, 1), fill=(114, 114, 114)),
                           # T.RandomAffine(degrees=90, translate=(.2, .2), scale=(1 / 1.5, 1.5),
                           #                shear=(-1, 1, -1, 1), fill=(114, 114, 114)),
                           T.Resize([imgsz, imgsz]),  # very slow
                           T.ToTensor(),
                           T.Normalize((0.5, 0.5, 0.5), (0.25, 0.25, 0.25))])  # PILImage from [0, 1] to [-1, 1]
    testform = T.Compose(trainform.transforms[-3:])

    # Dataloaders
    trainset = torchvision.datasets.ImageFolder(root=f'../{data}/train', transform=trainform)
    trainloader = torch.utils.data.DataLoader(trainset, batch_size=bs, shuffle=True, num_workers=nw)
    testset = torchvision.datasets.ImageFolder(root=f'../{data}/test', transform=testform)
    testloader = torch.utils.data.DataLoader(testset, batch_size=bs, shuffle=False, num_workers=nw)
    names = trainset.classes
    nc = len(names)
    print(f'Training {opt.model} on {data} dataset with {nc} classes...')

    # Show images
    # images, labels = iter(trainloader).next()
    # imshow(torchvision.utils.make_grid(images[:16]))
    # print(' '.join('%5s' % names[labels[j]] for j in range(16)))

    # Model
    if opt.model.startswith('yolov5'):
        # YOLOv5 Classifier
        model = torch.hub.load('ultralytics/yolov5', opt.model, pretrained=True, autoshape=False).model
        model.model = model.model[:8]
        m = model.model[-1]  # last layer
        ch = m.conv.in_channels if hasattr(m, 'conv') else sum([x.in_channels for x in m.m])  # ch into module
        c = Classify(ch, nc)  # Classify()
        c.i, c.f, c.type = m.i, m.f, 'models.common.Classify'  # index, from, type
        model.model[-1] = c  # replace
        for p in model.parameters():
            p.requires_grad = True  # for training
        input(model)
    elif opt.model in torch.hub.list('rwightman/gen-efficientnet-pytorch'):  # i.e. efficientnet_b0
        model = torch.hub.load('rwightman/gen-efficientnet-pytorch', opt.model, pretrained=True)
        model.classifier = nn.Linear(model.classifier.in_features, nc)
    else:  # try torchvision
        model = torchvision.models.__dict__[opt.model](pretrained=True)
        model.fc = nn.Linear(model.fc.weight.shape[1], nc)

    # print(model)  # debug
    model_info(model)

    # Optimizer
    lr0 = 0.0001 * bs  # intial lr
    lrf = 0.01  # final lr (fraction of lr0)
    if opt.adam:
        optimizer = optim.Adam(model.parameters(), lr=lr0 / 10)
    else:
        optimizer = optim.SGD(model.parameters(), lr=lr0, momentum=0.9, nesterov=True)

    # Scheduler
    lf = lambda x: ((1 + math.cos(x * math.pi / epochs)) / 2) * (1 - lrf) + lrf  # cosine
    scheduler = lr_scheduler.LambdaLR(optimizer, lr_lambda=lf)

    # Train
    model = model.to(device)
    criterion = nn.CrossEntropyLoss()  # loss function
    best_fitness = 0.
    # scaler = amp.GradScaler(enabled=cuda)
    print(f'Image sizes {imgsz} train, {imgsz} test\n'
          f'Using {nw} dataloader workers\n'
          f'Logging results to {save_dir}\n'
          f'Starting training for {epochs} epochs...\n\n'
          f"{'epoch':10s}{'gpu_mem':10s}{'train_loss':12s}{'val_loss':12s}{'accuracy':12s}")
    for epoch in range(epochs):  # loop over the dataset multiple times
        mloss = 0.  # mean loss
        model.train()
        pbar = tqdm(enumerate(trainloader), total=len(trainloader))  # progress bar
        for i, (images, labels) in pbar:
            images, labels = images.to(device), labels.to(device)

            # Forward
            with amp.autocast(enabled=cuda):
                loss = criterion(model(images), labels)

            # Backward
            loss.backward()  # scaler.scale(loss).backward()

            # Optimize
            optimizer.step()  # scaler.step(optimizer); scaler.update()
            optimizer.zero_grad()

            # Print
            mloss += loss.item()
            mem = '%.3gG' % (torch.cuda.memory_reserved() / 1E9 if torch.cuda.is_available() else 0)  # (GB)
            pbar.desc = f"{'%s/%s' % (epoch + 1, epochs):10s}{mem:10s}{mloss / (i + 1):<12.3g}"

            # Test
            if i == len(pbar) - 1:
                fitness = test(model, testloader, names, criterion, pbar=pbar)  # test

        # Scheduler
        scheduler.step()

        # Best fitness
        if fitness > best_fitness:
            best_fitness = fitness

        # Save model
        final_epoch = epoch + 1 == epochs
        if (not opt.nosave) or final_epoch:
            ckpt = {'epoch': epoch,
                    'best_fitness': best_fitness,
                    'model': deepcopy(model.module if is_parallel(model) else model).half(),
                    'optimizer': None}

            # Save last, best and delete
            torch.save(ckpt, last)
            if best_fitness == fitness:
                torch.save(ckpt, best)
            del ckpt

    # Train complete
    if final_epoch:
        print(f'Training complete. Results saved to {save_dir}.')

    # Show predictions
    images, labels = iter(testloader).next()
    predicted = torch.max(model(images), 1)[1]
    imshow(torchvision.utils.make_grid(images))
    print('GroundTruth: ', ' '.join('%5s' % names[labels[j]] for j in range(4)))
    print('Predicted: ', ' '.join('%5s' % names[predicted[j]] for j in range(4)))


def test(model, dataloader, names, criterion=None, verbose=False, pbar=None):
    model.eval()
    pred, targets, loss = [], [], 0
    with torch.no_grad():
        for images, labels in dataloader:
            images, labels = images.to(device), labels.to(device)
            y = model(images)
            pred.append(torch.max(y, 1)[1])
            targets.append(labels)
            if criterion:
                loss += criterion(y, labels)

    pred, targets = torch.cat(pred), torch.cat(targets)
    correct = (targets == pred).float()

    if pbar:
        pbar.desc += f"{loss / len(dataloader):<12.3g}{correct.mean().item():<12.3g}"

    accuracy = correct.mean().item()
    if verbose:  # all classes
        print(f"{'class':10s}{'number':10s}{'accuracy':10s}")
        print(f"{'all':10s}{correct.shape[0]:10d}{accuracy:10.5g}")
        for i, c in enumerate(names):
            t = correct[targets == i]
            print(f"{c:10s}{t.shape[0]:10d}{t.mean().item():10.5g}")

    return accuracy


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=str, default='yolov5s', help='initial weights path')
    parser.add_argument('--data', type=str, default='mnist', help='cifar10, cifar100 or mnist')
    parser.add_argument('--hyp', type=str, default='data/hyps/hyp.scratch.yaml', help='hyperparameters path')
    parser.add_argument('--epochs', type=int, default=20)
    parser.add_argument('--batch-size', type=int, default=128, help='total batch size for all GPUs')
    parser.add_argument('--img-size', type=int, default=64, help='train, test image sizes (pixels)')
    parser.add_argument('--nosave', action='store_true', help='only save final checkpoint')
    parser.add_argument('--adam', action='store_true', help='use torch.optim.Adam() optimizer')
    parser.add_argument('--evolve', action='store_true', help='evolve hyperparameters')
    parser.add_argument('--cache-images', action='store_true', help='cache images for faster training')
    parser.add_argument('--device', default='', help='cuda device, i.e. 0 or 0,1,2,3 or cpu')
    parser.add_argument('--workers', type=int, default=4, help='maximum number of dataloader workers')
    parser.add_argument('--project', default='runs/train', help='save to project/name')
    parser.add_argument('--name', default='exp', help='save to project/name')
    parser.add_argument('--exist-ok', action='store_true', help='existing project/name ok, do not increment')
    opt = parser.parse_args()

    # Checks
    check_git_status()
    check_requirements()

    # Parameters
    device = select_device(opt.device, batch_size=opt.batch_size)
    cuda = device.type != 'cpu'
    opt.hyp = check_file(opt.hyp)  # check files
    opt.save_dir = increment_path(Path(opt.project) / opt.name, exist_ok=opt.exist_ok | opt.evolve)  # increment run

    # # Train
    # train()

    # The following code below integrates with the test function
    data = opt.data
    imgsz = opt.img_size
    bs = opt.batch_size
    nw = min(os.cpu_count(), opt.workers)
    # Transforms
    testform = T.Compose([
                           T.Resize([imgsz, imgsz]),  # very slow
                           T.ToTensor(),
                           T.Normalize((0.5, 0.5, 0.5), (0.25, 0.25, 0.25))])  # PILImage from [0, 1] to [-1, 1]

    # Dataloader
    testset = torchvision.datasets.ImageFolder(root=f'../{data}/test', transform=testform)
    testloader = torch.utils.data.DataLoader(testset, batch_size=bs, shuffle=False, num_workers=nw)
    names = testset.classes
    nc = len(names)
    if opt.model.startswith('yolov5'):
        # YOLOv5 Classifier
        model = torch.hub.load('ultralytics/yolov5', opt.model, pretrained=True, autoshape=False).model
        model.model = model.model[:8]
        m = model.model[-1]  # last layer
        ch = m.conv.in_channels if hasattr(m, 'conv') else sum([x.in_channels for x in m.m])  # ch into module
        c = Classify(ch, nc)  # Classify()
        c.i, c.f, c.type = m.i, m.f, 'models.common.Classify'  # index, from, type
        model.model[-1] = c  # replace
    else:
        model = torch.load(opt.model)['model'].cpu().float()
    model = model.to(device)
    
    print(f'Testing {opt.model} on {data} dataset with {nc} classes...')
    test(model, testloader, names, verbose=True)
