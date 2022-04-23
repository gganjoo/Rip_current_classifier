# YOLOv5 🚀 by Ultralytics, GPL-3.0 license

# Start FROM Nvidia PyTorch image https://ngc.nvidia.com/catalog/containers/nvidia:pytorch
FROM ultralytics/yolov5:latest

# Install python dependencies
RUN pip install pafy
RUN pip install youtube_dl
RUN pip install -e git+git://github.com/mohamed-challal/pafy.git@develop#egg=pafy

# Copy contents
COPY . /usr/src/app

