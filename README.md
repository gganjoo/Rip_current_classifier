# W251 Rip Current Detection Project


This is a repo for our final project in W251 in the MIDS program. We attempt to detect riptides from video streams of the coast.

## Model Inference on the Cloud
1. Provision a `g4dn.2xlarge` instance on the cloud, using the Deep Learning AMI (Ubuntu 18.04) Version 52.0 as a base image. Expose port 22 for ssh
2. Ssh into the new instance on shell
3. Pull docker container:
```bash
docker pull rbector/w251-rip-current-project
```
4. Run docker container 
```bash
docker run -it --gpus all rbector/w251-rip-current-project
```
5. Start rip current detection program on the docker container, providing the appropriate input youtube stream.
```bash
python detect.py --source <youtube_stream_url> --weights best.pt --frames 90
```
6. Provide `riptide.detection@gmail.com` password and notificatione email when prompted.

## Drone and Streaming
1. Clone this repo to your Jetson
```bash
git clone <repo address>
```
3. Cd into the repo
```bash
cd w251-rip-current-project
```
5. Install dependencies
```bash
pip3 -r tello_requiremnts.txt
```
7. Install Redis
```bash
sudo apt install redis-server
```
9. Connect to Tello drone wifi
10. Start the tello.py script to control and stream video
```bash
python3 tello.py
```
12. On the machine that is connected to the internet, run FFMPEG pipline
```bash
ffmpeg -f lavfi -i anullsrc -rtsp_transport udp -i rtsp://192.168.55.1:6969/rip -tune /
zerolatency -vcodec libx264 -t 12:00:00 -pix_fmt + -c:v copy -c:a aac -strict experimental /
-f flv rtmp://a.rtmp.youtube.com/live2/<youtube live key>
```

## Drone Commands

- Left Shift -> Toggle Keyboard controls
- Right Shft -> Take off the drone
- Space -> Land drone
- Up arrow -> Increase Altitude
- Down arrow -> Decrease Altitude
- Left arrow -> Pan left
- Right arrow -> Pan right
- w -> Move forward
- a -> Move left
- s -> Move down
- d -> Move right

## Model Misc. Details

The train.ipynb has the training results.  The test.ipynb has the test/double check results.  The classifier.py file has comments for the train and test functions.
