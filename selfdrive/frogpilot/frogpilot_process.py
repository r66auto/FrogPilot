import datetime
import os
import threading

from cereal import log, messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Priority, config_realtime_process
from openpilot.common.time import system_time_valid
from openpilot.system.hardware import HARDWARE

from openpilot.selfdrive.frogpilot.controls.frogpilot_planner import FrogPilotPlanner
from openpilot.selfdrive.frogpilot.controls.lib.frogpilot_functions import backup_toggles, is_url_pingable
from openpilot.selfdrive.frogpilot.controls.lib.frogpilot_tracking import FrogPilotTracking
from openpilot.selfdrive.frogpilot.controls.lib.frogpilot_variables import FrogPilotVariables
from openpilot.selfdrive.frogpilot.controls.lib.model_manager import DEFAULT_MODEL, DEFAULT_MODEL_NAME, ModelManager
from openpilot.selfdrive.frogpilot.controls.lib.theme_manager import ThemeManager

OFFLINE = log.DeviceState.NetworkType.none

locks = {
  "backup_toggles": threading.Lock(),
  "download_all_models": threading.Lock(),
  "download_model": threading.Lock(),
  "download_theme": threading.Lock(),
  "time_checks": threading.Lock(),
  "update_frogpilot_params": threading.Lock(),
  "update_models": threading.Lock(),
  "update_themes": threading.Lock()
}

running_threads = {}

def run_thread_with_lock(name, target, args=()):
  if not running_threads.get(name, threading.Thread()).is_alive():
    with locks[name]:
      thread = threading.Thread(target=target, args=args)
      thread.start()
      running_threads[name] = thread

def automatic_update_check(started, params):
  update_available = params.get_bool("UpdaterFetchAvailable")
  update_ready = params.get_bool("UpdateAvailable")
  update_state_idle = params.get("UpdaterState", encoding='utf8') == "idle"

  if update_ready and not started:
    HARDWARE.reboot()
  elif update_available:
    os.system("pkill -SIGHUP -f system.updated.updated")
  elif update_state_idle:
    os.system("pkill -SIGUSR1 -f system.updated.updated")

def download_assets(model_manager, theme_manager, params, params_memory):
  model_to_download = params_memory.get("ModelToDownload", encoding='utf-8')
  if model_to_download:
    run_thread_with_lock("download_model", model_manager.download_model, (model_to_download,))

  if params_memory.get_bool("DownloadAllModels"):
    run_thread_with_lock("download_all_models", model_manager.download_all_models)

  color_to_download = params_memory.get("ColorToDownload", encoding='utf-8')
  if color_to_download:
    run_thread_with_lock("download_theme", theme_manager.download_theme, ("colors", color_to_download, "ColorToDownload"))

  distance_icon_to_download = params_memory.get("DistanceIconToDownload", encoding='utf-8')
  if distance_icon_to_download:
    run_thread_with_lock("download_theme", theme_manager.download_theme, ("distance_icons", distance_icon_to_download, "DistanceIconToDownload"))

  icon_to_download = params_memory.get("IconToDownload", encoding='utf-8')
  if icon_to_download:
    run_thread_with_lock("download_theme", theme_manager.download_theme, ("icons", icon_to_download, "IconToDownload"))

  signal_to_download = params_memory.get("SignalToDownload", encoding='utf-8')
  if signal_to_download:
    run_thread_with_lock("download_theme", theme_manager.download_theme, ("signals", signal_to_download, "SignalToDownload"))

  sound_to_download = params_memory.get("SoundToDownload", encoding='utf-8')
  if sound_to_download:
    run_thread_with_lock("download_theme", theme_manager.download_theme, ("sounds", sound_to_download, "SoundToDownload"))

  wheel_to_download = params_memory.get("WheelToDownload", encoding='utf-8')
  if wheel_to_download:
    run_thread_with_lock("download_theme", theme_manager.download_theme, ("steering_wheels", wheel_to_download, "WheelToDownload"))

def time_checks(automatic_updates, deviceState, model_manager, now, started, params, params_memory):
  if deviceState.networkType == OFFLINE:
    return

  if not is_url_pingable("https://github.com"):
    return

  screen_off = deviceState.screenBrightnessPercent == 0
  if automatic_updates and screen_off:
    automatic_update_check(started, params)

  update_maps(now, params, params_memory)

  with locks["update_models"]:
    model_manager.update_models(boot_run=False)

def update_maps(now, params, params_memory):
  maps_selected = params.get("MapsSelected", encoding='utf8')
  if not maps_selected:
    return

  day = now.day
  is_first = day == 1
  is_Sunday = now.weekday() == 6
  schedule = params.get_int("PreferredSchedule")

  maps_downloaded = os.path.exists('/data/media/0/osm/offline')
  if maps_downloaded and (schedule == 0 or (schedule == 1 and not is_Sunday) or (schedule == 2 and not is_first)):
    return

  suffix = "th" if 4 <= day <= 20 or 24 <= day <= 30 else ["st", "nd", "rd"][day % 10 - 1]
  todays_date = now.strftime(f"%B {day}{suffix}, %Y")

  if params.get("LastMapsUpdate", encoding='utf-8') == todays_date:
    return

  if params.get("OSMDownloadProgress", encoding='utf-8') is None:
    params_memory.put_nonblocking("OSMDownloadLocations", maps_selected)
    params.put_nonblocking("LastMapsUpdate", todays_date)

def frogpilot_thread():
  config_realtime_process(5, Priority.CTRL_LOW)

  frogpilot_toggles = FrogPilotVariables.toggles

  params = Params()
  params_memory = Params("/dev/shm/params")
  params_storage = Params("/persist/params")

  frogpilot_planner = FrogPilotPlanner()
  frogpilot_tracking = FrogPilotTracking()
  model_manager = ModelManager()
  theme_manager = ThemeManager()

  theme_manager.update_active_theme(frogpilot_toggles, params)

  run_time_checks = False
  started_previously = False
  time_validated = system_time_valid()
  update_toggles = False

  pm = messaging.PubMaster(['frogpilotPlan'])
  sm = messaging.SubMaster(['carState', 'controlsState', 'deviceState', 'frogpilotCarControl',
                            'frogpilotCarState', 'frogpilotNavigation', 'modelV2', 'radarState'],
                            poll='modelV2', ignore_avg_freq=['radarState'])

  while True:
    sm.update()

    now = datetime.datetime.now()
    deviceState = sm['deviceState']
    started = deviceState.started

    if not started and started_previously:
      frogpilot_planner = FrogPilotPlanner()
      frogpilot_tracking = FrogPilotTracking()

    if started and sm.updated['modelV2']:
      frogpilot_planner.update(sm['carState'], sm['controlsState'], sm['frogpilotCarControl'], sm['frogpilotCarState'],
                               sm['frogpilotNavigation'], sm['modelV2'], sm['radarState'], frogpilot_toggles)
      frogpilot_planner.publish(sm, pm, frogpilot_toggles)

      frogpilot_tracking.update(sm['carState'])

    if FrogPilotVariables.toggles_updated:
      update_toggles = True
    elif update_toggles:
      run_thread_with_lock("update_frogpilot_params", FrogPilotVariables.update_frogpilot_params, (started, True,))

      if not frogpilot_toggles.model_manager:
        params.put_nonblocking("Model", DEFAULT_MODEL)
        params.put_nonblocking("ModelName", DEFAULT_MODEL_NAME)

      if frogpilot_toggles.personalize_openpilot:
        theme_manager.update_active_theme(frogpilot_toggles, params)

      if time_validated and not started:
        run_thread_with_lock("backup_toggles", backup_toggles, (params, params_storage))

      update_toggles = False

    started_previously = started

    download_assets(model_manager, theme_manager, params, params_memory)

    if now.second == 0:
      run_time_checks = True
    elif run_time_checks or not time_validated:
      run_thread_with_lock("time_checks", time_checks, (frogpilot_toggles.automatic_updates, deviceState, model_manager, now, started, params, params_memory))
      run_thread_with_lock("update_themes", theme_manager.update_themes)
      run_time_checks = False

      if not time_validated:
        time_validated = system_time_valid()
        if not time_validated:
          continue
        run_thread_with_lock("update_models", model_manager.update_models)
        run_thread_with_lock("update_themes", theme_manager.update_themes)

      theme_manager.update_holiday()

def main():
  frogpilot_thread()

if __name__ == "__main__":
  main()
