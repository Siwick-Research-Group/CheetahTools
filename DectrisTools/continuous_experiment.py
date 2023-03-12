import warnings
from argparse import ArgumentParser
from random import shuffle
import io
from time import sleep, time
from datetime import datetime
import requests
from os import rename, path, getcwd, mkdir
import numpy as np
from tqdm import tqdm
from PIL import Image
from uedinst.cheetah import Cheetah
from uedinst.shutter import SC10Shutter
from uedinst.delay_stage import XPSController
from . import IP, PORT, TIMESTAMP_FORMAT


warnings.simplefilter("ignore", ResourceWarning)

DIR_PUMP_OFF = "pump_off"
DIR_LASER_BG = "laser_background"
DIR_DARK = "dark_image"
T0_POS = 100


def parse_args():
    parser = ArgumentParser(description="script to take single shot experiment")
    parser.add_argument("--cheetah_ip", type=str, default=IP, help="cheetah ip address")
    parser.add_argument("--cheetah_port", type=int, default=PORT, help="cheetah port")
    parser.add_argument(
        "--pump_shutter_port",
        type=str,
        default="COM27",
        help="com port of the shutter controller for the pump shutter",
    )
    parser.add_argument(
        "--probe_shutter_port",
        type=str,
        default="COM17",
        help="com port of the shutter controller for the probe shutter",
    )
    parser.add_argument(
        "--delay_stage_ip",
        type=str,
        default="192.168.254.254",
        help="ip address of the delay stage",
    )
    parser.add_argument("--savedir", type=str, help="save directory")
    parser.add_argument("--n_scans", type=int, help="number of scans")
    parser.add_argument("--delays", type=str)
    parser.add_argument("--exposure", type=float, default=10, help="exposure time of each image")
    args = parser.parse_args()
    return args


def parse_timedelays(time_str):
    # shamelessly stolen from faraday
    time_str = str(time_str)
    elements = time_str.split(",")
    if not elements:
        return []
    timedelays = []

    # Two possibilities : floats or ranges
    # Either elem = float
    # or     elem = start:step:stop
    for elem in elements:
        try:
            fl = float(elem)
            timedelays.append(fl)
        except ValueError:
            try:
                start, step, stop = tuple(map(float, elem.split(":")))
                fl = np.round(np.arange(start, stop, step), 3).tolist()
                timedelays.extend(fl)
            except:
                return []

    # Round timedelays down to the femtosecond
    timedelays = map(lambda n: round(n, 3), timedelays)

    return list(sorted(timedelays))


def acquire_image(detector, savedir, scandir, filename):
    exception = None
    try:
        detector.preview()
        sleep(0.1)
        while True:
            sleep(0.05)
            detector._Cheetah__update_info()
            if detector.Measurement.Info.Status == "DA_IDLE":
                break

        response = requests.get(url=detector.url + "/measurement/image")
        img = Image.open(io.BytesIO(response.content))
        img.save(path.join(savedir, scandir, filename))

    except TimeoutError as exception:
        detector.stop()
    return exception


def fmt_log(message):
    return f"{datetime.now().strftime(TIMESTAMP_FORMAT)} | {message}\n"


def append_to_log(logfile, msg):
    with open(logfile, "a") as f:
        f.write(msg)


def move_stages_to_time(xps, time):
    new_pos =  xps.delay_stage.delay_to_distance(time) + T0_POS
    if new_pos <= -100:
        xps.compensation_stage.absolute_move(-120)
        xps.delay_stage.absolute_time(time - xps.delay_stage.distance_to_delay(-120), T0_POS)
    elif new_pos <= 100:
        xps.compensation_stage.absolute_move(0)
        xps.delay_stage.absolute_time(time, T0_POS)
    else:
        xps.compensation_stage.absolute_move(120)
        xps.delay_stage.absolute_time(time - xps.delay_stage.distance_to_delay(120), T0_POS)


def run(cmd_args):
    if cmd_args.savedir is None:
        cmd_args.savedir = getcwd()
    savedir = cmd_args.savedir
    delays = parse_timedelays(cmd_args.delays)

    # prepare hardware for experiment
    C = Cheetah(cmd_args.cheetah_ip, cmd_args.cheetah_port)
    C._Cheetah__update_info()

    C.Detector.Config.nTriggers = 1
    C.Detector.Config.TriggerMode = "AUTOTRIGSTART_TIMERSTOP"
    C._Cheetah__update_info()
    if cmd_args.exposure > C.Detector.Config.TriggerPeriod + 0.002:
        C.Detector.Config.TriggerPeriod = cmd_args.exposure + 0.002  # hardware limitation
        C.Detector.Config.ExposureTime = cmd_args.exposure
    else:
        C.Detector.Config.ExposureTime = cmd_args.exposure
        C.Detector.Config.TriggerPeriod = cmd_args.exposure + 0.002  # hardware limitation

    s_pump = SC10Shutter(args.pump_shutter_port)
    s_pump.set_operating_mode("manual")
    s_probe = SC10Shutter(args.probe_shutter_port)
    s_probe.set_operating_mode("manual")

    xps = XPSController(cmd_args.delay_stage_ip)

    # start experiment
    log_filename = path.join(savedir, "experiment.log")
    logfile = open(log_filename, "w+")
    logfile.write(fmt_log(f"starting experiment with {cmd_args.n_scans} scans at {len(delays)} delays"))
    logfile.close()
    try:
        try:
            mkdir(path.join(savedir, DIR_LASER_BG))
        except FileExistsError:
            pass
        try:
            mkdir(path.join(savedir, DIR_PUMP_OFF))
        except FileExistsError:
            pass
        try:
            mkdir(path.join(savedir, DIR_DARK))
        except FileExistsError:
            pass
        for i in tqdm(range(cmd_args.n_scans), desc="scans", disable=False):
            s_pump.enable(False)
            s_probe.enable(False)
            while True:
                exception = acquire_image(C, savedir, DIR_DARK, f"dark_epoch_{time():010.0f}s.tif")
                if exception:
                    append_to_log(log_filename, fmt_log(str(exception)))
                else:
                    break
            s_pump.enable(True)
            s_probe.enable(False)
            while True:
                exception = acquire_image(C, savedir, DIR_LASER_BG, f"laser_bg_epoch_{time():010.0f}s.tif")
                if exception:
                    append_to_log(log_filename, fmt_log(str(exception)))
                else:
                    break
            append_to_log(log_filename, fmt_log("laser background image acquired"))
            s_pump.enable(False)
            s_probe.enable(True)
            while True:
                exception = acquire_image(C, savedir, DIR_PUMP_OFF, f"pump_off_epoch_{time():010.0f}s.tif")
                if exception:
                    append_to_log(log_filename, fmt_log(str(exception)))
                else:
                    break
            append_to_log(log_filename, fmt_log("pump off image acquired"))
            s_pump.enable(True)

            scandir = f"scan_{i+1:04d}"
            mkdir(path.join(savedir, scandir))
            shuffle(delays)
            for delay in tqdm(delays, leave=False, desc="delay steps", disable=False):
                filename = f"pumpon_{delay:+010.3f}ps.tif"

                move_stages_to_time(xps, delay)
                xps.delay_stage._wait_end_of_move()
                while True:
                    exception = acquire_image(C, savedir, scandir, filename)
                    if exception:
                        append_to_log(log_filename, fmt_log(str(exception)))
                    else:
                        break
                append_to_log(log_filename, fmt_log(f"pump on image acquired at scan {i+1} and time-delay {delay:.1f}ps"))

        s_pump.enable(False)
        s_probe.enable(False)
        append_to_log(log_filename, fmt_log("EXPERIMENT COMPLETE"))
        print("🍻")
    except Exception as e:
        append_to_log(log_filename, fmt_log(str(e)))
        raise e


if __name__ == "__main__":
    # TEST COMMAND:
    # python -m DectrisTools.single_shot_experiment --savedir="D:\Data\Tests\single_shot_exp" --images_per_datapoint=1000 --n_scans=3 --delays="0:1:5"
    args = parse_args()
    run(args)
