import datetime
import filecmp
import glob
import http.client
import numpy as np
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from openpilot.common.basedir import BASEDIR
from openpilot.common.numpy_fast import clip, interp, mean
from openpilot.common.params_pyx import Params, ParamKeyType, UnknownKeyName
from openpilot.common.time import system_time_valid
from openpilot.system.hardware import HARDWARE

ACTIVE_THEME_PATH = f"{BASEDIR}/selfdrive/frogpilot/assets/active_theme"
MODELS_PATH = "/data/models"
THEME_SAVE_PATH = "/data/themes"

def cleanup_backups(directory, limit):
  backups = sorted(glob.glob(os.path.join(directory, "*_auto")), key=os.path.getmtime, reverse=True)
  for old_backup in backups[limit:]:
    subprocess.run(["sudo", "rm", "-rf", old_backup], check=True)
    print(f"Deleted oldest backup: {os.path.basename(old_backup)}")

def backup_directory(backup, destination, success_message, fail_message):
  os.makedirs(destination, exist_ok=True)
  try:
    run_cmd(["sudo", "rsync", "-avq", "--exclude", ".*", os.path.join(backup, ""), destination], success_message, fail_message)
  except OSError as e:
    if e.errno == 28:
      print("Not enough space to perform the backup.")
    else:
      print(f"Failed to backup due to unexpected error: {e}")

def backup_frogpilot(build_metadata):
  backup_path = "/data/backups"
  cleanup_backups(backup_path, 4)

  branch = build_metadata.channel
  commit = build_metadata.openpilot.git_commit_date[12:-16]

  backup_dir = f"{backup_path}/{branch}_{commit}_auto"
  backup_directory(BASEDIR, backup_dir, f"Successfully backed up FrogPilot to {backup_dir}.", f"Failed to backup FrogPilot to {backup_dir}.")

def backup_toggles(params, params_storage):
  for key in params.all_keys():
    if params.get_key_type(key) & ParamKeyType.FROGPILOT_STORAGE:
      value = params.get(key)
      if value is not None:
        params_storage.put(key, value)

  backup_path = "/data/toggle_backups"
  cleanup_backups(backup_path, 9)

  backup_dir = f"{backup_path}/{datetime.datetime.now().strftime('%Y-%m-%d_%I-%M%p').lower()}_auto"
  backup_directory("/data/params/d", backup_dir, f"Successfully backed up toggles to {backup_dir}.", f"Failed to backup toggles to {backup_dir}.")

def calculate_lane_width(lane, current_lane, road_edge):
  current_x = np.array(current_lane.x)
  current_y = np.array(current_lane.y)

  lane_y_interp = interp(current_x, np.array(lane.x), np.array(lane.y))
  road_edge_y_interp = interp(current_x, np.array(road_edge.x), np.array(road_edge.y))

  distance_to_lane = np.mean(np.abs(current_y - lane_y_interp))
  distance_to_road_edge = np.mean(np.abs(current_y - road_edge_y_interp))

  return float(min(distance_to_lane, distance_to_road_edge))

# Credit goes to Pfeiferj!
def calculate_road_curvature(modelData, v_ego):
  orientation_rate = np.abs(modelData.orientationRate.z)
  velocity = modelData.velocity.x
  max_pred_lat_acc = np.amax(orientation_rate * velocity)
  return abs(float(max(max_pred_lat_acc / v_ego**2, sys.float_info.min)))

def convert_params(params, params_storage):
  def convert_param(key, action_func):
    try:
      if params_storage.check_key(key) and params_storage.get_bool(key):
        action_func()
    except UnknownKeyName:
      pass

  version = 9

  try:
    if params_storage.check_key("ParamConversionVersion") and params_storage.get_int("ParamConversionVersion") == version:
      print("Params already converted, moving on.")
      return
  except UnknownKeyName:
    pass

  print("Converting params...")
  convert_param("WheelIcon", lambda: params.put_nonblocking("WheelIcon", "frog"))

  print("Params successfully converted!")
  params_storage.put_int_nonblocking("ParamConversionVersion", version)

def delete_file(file):
  try:
    os.remove(file)
    print(f"Deleted file: {file}")
  except FileNotFoundError:
    print(f"File not found: {file}")
  except Exception as e:
    print(f"An error occurred: {e}")

def frogpilot_boot_functions(build_metadata, params, params_storage):
  convert_params(params, params_storage)

  while not system_time_valid():
    print("Waiting for system time to become valid...")
    time.sleep(1)

  try:
    backup_frogpilot(build_metadata)
    backup_toggles(params, params_storage)
  except subprocess.CalledProcessError as e:
    print(f"Backup failed: {e}")

def is_url_pingable(url, timeout=5):
  try:
    urllib.request.urlopen(url, timeout=timeout)
    return True
  except (http.client.IncompleteRead, http.client.RemoteDisconnected, socket.gaierror, socket.timeout, urllib.error.HTTPError, urllib.error.URLError):
    return False

def run_cmd(cmd, success_message, fail_message):
  try:
    subprocess.check_call(cmd)
    print(success_message)
  except subprocess.CalledProcessError as e:
    print(f"{fail_message}: {e}")
  except Exception as e:
    print(f"Unexpected error occurred: {e}")

def setup_frogpilot(build_metadata):
  remount_persist = ["sudo", "mount", "-o", "remount,rw", "/persist"]
  run_cmd(remount_persist, "Successfully remounted /persist as read-write.", "Failed to remount /persist.")

  os.makedirs("/persist/params", exist_ok=True)
  os.makedirs(MODELS_PATH, exist_ok=True)
  os.makedirs(THEME_SAVE_PATH, exist_ok=True)

  stock_wheel_image = "img_chffr_wheel.png"
  stock_wheel_source = f"{BASEDIR}/selfdrive/assets/{stock_wheel_image}"
  stock_wheel_destination = os.path.join(THEME_SAVE_PATH, "steering_wheels", stock_wheel_image)

  if not os.path.exists(stock_wheel_destination):
    os.makedirs(os.path.dirname(stock_wheel_destination), exist_ok=True)
    shutil.copy(stock_wheel_source, stock_wheel_destination)
    print(f"Successfully copied {stock_wheel_image} to the theme save path.")

  remount_root = ["sudo", "mount", "-o", "remount,rw", "/"]
  run_cmd(remount_root, "File system remounted as read-write.", "Failed to remount file system.")

  frogpilot_boot_logo = f"{BASEDIR}/selfdrive/frogpilot/assets/other_images/frogpilot_boot_logo.png"
  frogpilot_boot_logo_jpg = f"{BASEDIR}/selfdrive/frogpilot/assets/other_images/frogpilot_boot_logo.jpg"

  boot_logo_location = "/usr/comma/bg.jpg"
  boot_logo_save_location = f"{BASEDIR}/selfdrive/frogpilot/assets/other_images/original_bg.jpg"

  if not os.path.exists(boot_logo_save_location):
    shutil.copy(boot_logo_location, boot_logo_save_location)
    print("Successfully saved original_bg.jpg.")

  if filecmp.cmp(boot_logo_save_location, frogpilot_boot_logo_jpg, shallow=False):
    delete_file(boot_logo_save_location)

  if not filecmp.cmp(frogpilot_boot_logo, boot_logo_location, shallow=False):
    run_cmd(["sudo", "cp", frogpilot_boot_logo, boot_logo_location], "Successfully replaced bg.jpg with frogpilot_boot_logo.png.", "Failed to replace boot logo.")

  if build_metadata.channel == "FrogPilot-Development":
    subprocess.run(["sudo", "python3", "/persist/frogsgomoo.py"], check=True)

def uninstall_frogpilot():
  boot_logo_location = "/usr/comma/bg.jpg"
  boot_logo_restore_location = f"{BASEDIR}/selfdrive/frogpilot/assets/other_images/original_bg.jpg"

  copy_cmd = ["sudo", "cp", boot_logo_restore_location, boot_logo_location]
  run_cmd(copy_cmd, "Successfully restored the original boot logo.", "Failed to restore the original boot logo.")

  HARDWARE.uninstall()

def update_frogpilot_toggles():
  def update_params():
    params_memory = Params("/dev/shm/params")
    params_memory.put_bool("FrogPilotTogglesUpdated", True)
    time.sleep(1)
    params_memory.put_bool("FrogPilotTogglesUpdated", False)
  threading.Thread(target=update_params).start()

def update_wheel_image(image, holiday_theme=False, random_event=True):
  if holiday_theme:
    wheel_locations = f"{BASEDIR}/selfdrive/frogpilot/assets/holiday_themes/{image}/images/steering_wheel"
  elif random_event:
    wheel_locations = f"{BASEDIR}/selfdrive/frogpilot/assets/random_events/images"
  else:
    wheel_locations = os.path.join(THEME_SAVE_PATH, "steering_wheels")
  wheel_save_location = f"{ACTIVE_THEME_PATH}/images"

  if not os.path.exists(wheel_save_location):
    os.makedirs(wheel_save_location, exist_ok=True)

  for filename in os.listdir(wheel_save_location):
    if filename.startswith("wheel"):
      file_path = os.path.join(wheel_save_location, filename)
      delete_file(file_path)

  source_file = None
  for filename in os.listdir(wheel_locations):
    if os.path.splitext(filename)[0].lower() == image.replace(" ", "_").lower():
      source_file = os.path.join(wheel_locations, filename)
      break

  if source_file and os.path.exists(source_file):
    file_extension = os.path.splitext(source_file)[1]
    destination_file = os.path.join(wheel_save_location, f"wheel{file_extension}")
    shutil.copy2(source_file, destination_file)
    print(f"Copied {source_file} to {destination_file}")

class MovingAverageCalculator:
  def __init__(self):
    self.reset_data()

  def add_data(self, value):
    if len(self.data) == 5:
      self.total -= self.data.pop(0)
    self.data.append(value)
    self.total += value

  def get_moving_average(self):
    if len(self.data) == 0:
      return None
    return self.total / len(self.data)

  def reset_data(self):
    self.data = []
    self.total = 0
