"""
collection of helper classes and functions
"""
from time import sleep
import logging as log
import io
from collections import deque
import requests
from PyQt5.QtCore import pyqtSignal, pyqtSlot, QObject, QThread
from PyQt5.QtWidgets import QAction, QMenu
import numpy as np
import pyqtgraph as pg
from PIL import Image
from uedinst.cheetah import Cheetah


def monitor_to_array(bytestring):
    """
    image comes as a file-like object in tif format and is returned as a np.ndarray
    """
    return np.rot90(np.array(Image.open(io.BytesIO(bytestring))), k=3)


class CheetahImageGrabber(QObject):
    """
    class capable of setting the collecting images from the detector in a non-blocking fashion
    """

    image_ready = pyqtSignal(np.ndarray)
    exposure_triggered = pyqtSignal()
    connected = False

    def __init__(self, ip, port):
        super().__init__()

        self.C = Cheetah(ip, port)

        self.C._Cheetah__update_info()
        self.C.stop()
        self.C.Detector.Config.nTriggers = 1
        self.C.Detector.Config.TriggerMode = "AUTOTRIGSTART_TIMERSTOP"

        self.image_grabber_thread = QThread()
        self.moveToThread(self.image_grabber_thread)
        self.image_grabber_thread.started.connect(self.__get_image)

    @pyqtSlot()
    def __get_image(self):
        """
        image collection method
        """
        print("starting measurement")
        self.C._Cheetah__update_info()
        self.C.preview()
        if not self.C.Measurement.Info.Status == "DA_IDLE":
            print(self.C.Measurement.Info.Status)
            return

        response = requests.get(url=self.C.url + "/measurement/image")
        img = Image.open(io.BytesIO(response.content))
        self.image_ready.emit(np.array(img))

        self.image_grabber_thread.quit()
        log.debug(f"quit image_grabber_thread {self.image_grabber_thread.currentThread()}")

    def wait_for_state(self, state_name, logic=True):
        """
        making sure waiting for the detector to enter or leave a state is not blocking the interruption of the thread
        """
        log.debug(f"waiting for state: {state_name} to be {logic}")
        if logic:
            while self.Q.state == state_name:
                if self.image_grabber_thread.isInterruptionRequested():
                    self.image_grabber_thread.quit()
                    return
                sleep(0.05)
            return
        while self.Q.state != state_name:
            if self.image_grabber_thread.isInterruptionRequested():
                self.image_grabber_thread.quit()
                return
            sleep(0.05)

    @property
    def exposure(self):
        return self.C.Detector.Config.ExposureTime

    @exposure.setter
    def exposure(self, exposure_time):
        if exposure_time > self.C.Detector.Config.TriggerPeriod + 0.002:
            self.C.Detector.Config.TriggerPeriod = exposure_time + 0.002  # hardware limitation
            self.C.Detector.Config.ExposureTime = exposure_time
        else:
            self.C.Detector.Config.ExposureTime = exposure_time
            self.C.Detector.Config.TriggerPeriod = exposure_time + 0.002  # hardware limitation


class CheetahStatusGrabber(QObject):
    status_ready = pyqtSignal(dict)
    connected = False

    def __init__(self, ip, port):
        super().__init__()

        self.C = Cheetah(ip, port)
        self.C._Cheetah__update_info()

        self.status_grabber_thread = QThread()
        self.moveToThread(self.status_grabber_thread)
        self.status_grabber_thread.started.connect(self.__get_status)

    @pyqtSlot()
    def __get_status(self):
        self.C._Cheetah__update_info()
        log.debug(f"started status_grabber_thread {self.status_grabber_thread.currentThread()}")
        self.status_ready.emit(
            {
                "state": self.C.Measurement.Info.Status,
                "exposure": self.C.Detector.Config.ExposureTime,
            }
        )
        self.status_grabber_thread.quit()
        log.debug(f"quit status_grabber_thread {self.status_grabber_thread.currentThread()}")


def interrupt_acquisition(f):
    """
    decorator interrupting/resuming image acquisition before/after function call
    """

    def wrapper(self):
        log.debug("stopping liveview")
        self.image_timer.stop()
        if self.cheetah_image_grabber.connected:
            if not self.cheetah_image_grabber.image_grabber_thread.isFinished():
                log.debug("aborting acquisition")
                self.cheetah_image_grabber.C.stop()
                self.cheetah_image_grabber.image_grabber_thread.requestInterruption()
                self.cheetah_image_grabber.image_grabber_thread.wait()
        f(self)
        if self.actionStart.isChecked():
            log.debug("restarting liveview")
            self.image_timer.start(self.update_interval)

    return wrapper


class RectROI(pg.RectROI):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.win = pg.GraphicsLayoutWidget(title="ROI integrated intensity")
        self.plot = self.win.addPlot()
        self.plot.axes["left"]["item"].setLabel("mean intensity")
        self.plot.axes["bottom"]["item"].setLabel("image index")
        self.curve = self.plot.plot()
        self.last_means = deque(maxlen=30)

    def getMenu(self):
        if self.menu is None:
            self.menu = QMenu()
            self.menu.setTitle("ROI")
            rem_act = QAction("Remove ROI", self.menu)
            rem_act.triggered.connect(self.removeClicked)
            self.menu.addAction(rem_act)
            self.menu.rem_act = rem_act
            history_act = QAction("Show mean history", self.menu)
            history_act.triggered.connect(self.integral_plot_clicked)
            self.menu.addAction(history_act)
            self.menu.history_act = history_act
        self.menu.setEnabled(self.contextMenuEnabled())
        return self.menu

    def integral_plot_clicked(self):
        self.win.show()

    def add_mean(self, data, img):
        self.last_means.append(self.getArrayRegion(data, img).mean())
        self.curve.setData(x=range(-len(self.last_means) + 1, 1), y=self.last_means)
