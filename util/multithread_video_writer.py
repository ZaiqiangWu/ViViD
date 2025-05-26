import cv2
import numpy as np
from threading import Thread
import queue
import time
import imageio
import os
import gc

class MultithreadVideoWriter:
    def __init__(self, outvid, fps=30,quality=5):
        self.outvid = outvid
        self.quality=quality
        self.fps = fps
        self.vid = None
        self.buff_size = 160
        self.stop = False
        self.finished = False
        self.quit = False
        self.frame_queue = queue.Queue(maxsize=self.buff_size)
        self.t = Thread(target=self.stream2video, args=())
        self.t.daemon = True
        self.t.start()

    def append(self, image, isRGB=False):
        if not isRGB:
            image = image[:, :, [2, 1, 0]]
        self.frame_queue.put(image)

    def stream2video(self):
        while True:
            if self.stop and self.frame_queue.empty():
                self.finished = True
                break

            try:
                frame = self.frame_queue.get(timeout=1)
                if self.vid is None:
                    self.vid = imageio.get_writer(self.outvid, fps=self.fps, codec='libx264',quality=self.quality)
                self.vid.append_data(frame)
            except queue.Empty:
                continue

        while True:
            time.sleep(0.1)
            if self.quit:
                break

    def close(self):
        self.quit = True
        self.t.join()
        if self.vid is not None:
            self.vid.close()

    def make_video(self):
        self.stop = True
        while not self.finished:
            time.sleep(0.1)

        if self.vid is not None:
            self.vid.close()

        # Force garbage collection
        gc.collect()

