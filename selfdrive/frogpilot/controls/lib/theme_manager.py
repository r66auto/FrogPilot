import datetime
import os
import re
import requests
import subprocess

from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params

from openpilot.selfdrive.frogpilot.controls.lib.download_functions import GITHUB_URL, GITLAB_URL, get_remote_file_size, get_repository_url, verify_download
from openpilot.selfdrive.frogpilot.controls.lib.frogpilot_functions import THEME_SAVE_PATH, delete_file, update_frogpilot_toggles

class ThemeManager:
  def __init__(self):
    self.params = Params()
    self.params_memory = Params("/dev/shm/params")

    self.previous_theme_id = 0

  @staticmethod
  def calculate_easter(year):
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime.datetime(year, month, day)

  @staticmethod
  def calculate_thanksgiving(year):
    november_first = datetime.datetime(year, 11, 1)
    day_of_week = november_first.weekday()
    return november_first + datetime.timedelta(days=(3 - day_of_week + 21) % 7 + 21)

  @staticmethod
  def is_within_week_of(target_date, current_date):
    start_of_week = target_date - datetime.timedelta(days=target_date.weekday())
    end_of_week = start_of_week + datetime.timedelta(days=6)
    return start_of_week <= current_date <= end_of_week

  def update_holiday(self):
    current_date = datetime.datetime.now()
    year = current_date.year

    holidays = {
      "new_years": (datetime.datetime(year, 1, 1), 1),
      "valentines": (datetime.datetime(year, 2, 14), 2),
      "st_patricks": (datetime.datetime(year, 3, 17), 3),
      "world_frog_day": (datetime.datetime(year, 3, 20), 4),
      "april_fools": (datetime.datetime(year, 4, 1), 5),
      "easter_week": (self.calculate_easter(year), 6),
      "cinco_de_mayo": (datetime.datetime(year, 5, 5), 7),
      "fourth_of_july": (datetime.datetime(year, 7, 4), 8),
      "halloween_week": (datetime.datetime(year, 10, 31), 9),
      "thanksgiving_week": (self.calculate_thanksgiving(year), 10),
      "christmas_week": (datetime.datetime(year, 12, 25), 11)
    }

    for holiday, (date, theme_id) in holidays.items():
      if holiday.endswith("_week") and self.is_within_week_of(date, current_date) or current_date.date() == date.date():
        if theme_id != self.previous_theme_id:
          self.params_memory.put_int("CurrentHolidayTheme", theme_id)
          update_frogpilot_toggles()
        self.previous_theme_id = theme_id
        return

    if self.previous_theme_id != 0:
      self.params_memory.put_int("CurrentHolidayTheme", "0")
      update_frogpilot_toggles()
    self.previous_theme_id = 0

  def handle_error(self, destination, error_message, error):
    print(f"Error occurred: {error}")
    self.params_memory.put("WheelDownloadProgress", error_message)
    self.params_memory.remove("WheelToDownload")
    delete_file(destination)

  def download_file(self, destination, url):
    try:
      with requests.get(url, stream=True, timeout=5) as r:
        r.raise_for_status()
        total_size = get_remote_file_size(url)
        downloaded_size = 0

        with open(destination, 'wb') as f:
          for chunk in r.iter_content(chunk_size=8192):
            if self.params_memory.get_bool("CancelWheelDownload"):
              self.handle_error(destination, "Download cancelled...", "Download cancelled...")
              return

            if chunk:
              f.write(chunk)
              downloaded_size += len(chunk)
              progress = (downloaded_size / total_size) * 100

              if progress != 100:
                self.params_memory.put("WheelDownloadProgress", f"{progress:.0f}%")
              else:
                self.params_memory.put("WheelDownloadProgress", "Verifying authenticity...")

    except requests.HTTPError as http_error:
      if destination.endswith(".png") and http_error.response.status_code == 404:
        return
      self.handle_error(destination, f"Failed: Server error ({http_error.response.status_code})", http_error)
    except requests.ConnectionError as connection_error:
      self.handle_error(destination, "Failed: Connection dropped...", connection_error)
    except requests.Timeout as timeout_error:
      self.handle_error(destination, "Failed: Download timed out...", timeout_error)
    except requests.RequestException as request_error:
      self.handle_error(destination, "Failed: Network request error. Check connection.", request_error)
    except Exception as e:
      self.handle_error(destination, "Failed: Unexpected error.", e)

  def handle_existing_wheel(self, wheel):
    print(f"Wheel {wheel} already exists, skipping download...")
    self.params_memory.put("WheelDownloadProgress", "Wheel already exists...")
    self.params_memory.remove("WheelToDownload")

  def handle_verification_failure(self, wheel, wheel_path, wheel_url):
    if self.params_memory.get_bool("CancelWheelDownload"):
      self.handle_error(wheel_path, "Download cancelled...", "Download cancelled...")
      return

    self.handle_error(wheel_path, "Issue connecting to Github, trying Gitlab", f"Wheel {wheel} verification failed. Redownloading from Gitlab...")
    second_wheel_url = f"{GITLAB_URL}Steering-Wheels/{wheel}"
    self.download_file(wheel_path, second_wheel_url)

    if verify_download(wheel_path, second_wheel_url):
      print(f"Wheel {wheel} redownloaded and verified successfully from Gitlab.")
    else:
      print(f"Wheel {wheel} redownload verification failed from Gitlab.")

  def download_wheel(self, wheel_to_download, branch):
    wheel_to_download = wheel_to_download.lower()
    wheel_path = os.path.join(THEME_SAVE_PATH, "steering_wheels", wheel_to_download)

    if os.path.exists(wheel_path):
      self.handle_existing_wheel(wheel_to_download)
      return

    repo_url = get_repository_url()
    if repo_url is None:
      self.handle_error(wheel_path, "Github and Gitlab are offline...", "Github and Gitlab are offline...")
      return

    for ext in [".png", ".gif"]:
      wheel_url = f"{GITHUB_URL}{branch}/{wheel_to_download}{ext}"
      try:
        self.download_file(wheel_path + ext, wheel_url)
        if verify_download(wheel_path + ext, wheel_url):
          self.update_themes(branch)
          print(f"Wheel {wheel_to_download} downloaded and verified successfully!")
          self.params_memory.put("WheelDownloadProgress", "Downloaded!")
          self.params_memory.remove("WheelToDownload")
          return
      except Exception as e:
        continue

    self.handle_verification_failure(wheel_to_download, wheel_path, wheel_url)

  @staticmethod
  def fetch_wheel_files(branch_url, branch_name):
    try:
      api_url = f"https://api.github.com/repos/{branch_url}/contents?ref={branch_name}"
      response = requests.get(api_url)
      response.raise_for_status()
      return [file['name'] for file in response.json()]
    except Exception as e:
      print(f"Failed to fetch wheel files. Error: {e}")
      return []

  def update_wheel_params(self, wheel_files):
    wheel_dir = os.path.join(THEME_SAVE_PATH, "steering_wheels")
    available_files = os.listdir(wheel_dir)
    available_wheels = [wheel.replace('_', ' ').split('.')[0].title() for wheel in available_files if wheel != "img_chffr_wheel.png"]
    available_wheels.extend(["Stock", "None"])
    available_wheels.sort()
    self.params.put("AvailableWheels", ','.join(available_wheels))

    downloadable_wheels = [wheel.replace('_', ' ').split('.')[0].title() for wheel in wheel_files if wheel.replace('_', ' ').split('.')[0].title() not in available_wheels]
    downloadable_wheels.sort()
    self.params.put("DownloadableWheels", ','.join(downloadable_wheels))
    print("Wheels list updated successfully.")

  def update_themes(self, branch_name):
    repo_url = get_repository_url()
    if repo_url is None:
      handle_error(None, "Github and Gitlab are offline...", "Github and Gitlab are offline...", params_memory)
      return

    branch_url = "FrogAi/FrogPilot-Resources"
    wheel_files = self.fetch_wheel_files(branch_url, branch_name)
    if wheel_files:
      self.update_wheel_params(wheel_files)
