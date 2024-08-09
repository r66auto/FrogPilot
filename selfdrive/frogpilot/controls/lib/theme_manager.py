import datetime
import os
import re
import requests
import subprocess

from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params

from openpilot.selfdrive.frogpilot.controls.lib.download_functions import GITHUB_URL, GITLAB_URL, get_remote_file_size, get_repository_url, verify_download
from openpilot.selfdrive.frogpilot.controls.lib.frogpilot_functions import THEME_SAVE_PATH, delete_file, update_frogpilot_toggles, update_wheel_image

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
          update_wheel_image(theme_id, True, False)
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

  def handle_existing_theme(self, theme):
    print(f"Theme {theme} already exists, skipping download...")
    self.params_memory.put("WheelDownloadProgress", "Theme already exists...")
    self.params_memory.remove("WheelToDownload")

  def handle_verification_failure(self, theme, theme_path, theme_url):
    if self.params_memory.get_bool("CancelWheelDownload"):
      self.handle_error(theme_path, "Download cancelled...", "Download cancelled...")
      return

    self.handle_error(theme_path, "Issue connecting to Github, trying Gitlab", f"Theme {Theme} verification failed. Redownloading from Gitlab...")
    second_theme_url = f"{GITLAB_URL}Steering-Wheels/{Theme}"
    self.download_file(theme_path, second_theme_url)

    if verify_download(theme_path, second_theme_url):
      print(f"Theme {Theme} redownloaded and verified successfully from Gitlab.")
    else:
      print(f"Theme {Theme} redownload verification failed from Gitlab.")

  def download_theme(self, theme_to_download, branch):
    theme_to_download = theme_to_download.lower().replace(" ", "_")
    theme_path = os.path.join(THEME_SAVE_PATH, "steering_wheels", theme_to_download)

    if os.path.exists(theme_path):
      self.handle_existing_theme(theme_to_download)
      return

    repo_url = get_repository_url()
    if repo_url is None:
      self.handle_error(theme_path, "Github and Gitlab are offline...", "Github and Gitlab are offline...")
      return

    for ext in [".png", ".gif"]:
      theme_url = f"{GITHUB_URL}{branch}/{theme_to_download}{ext}"
      try:
        self.download_file(theme_path + ext, theme_url)
        if verify_download(theme_path + ext, theme_url):
          self.update_themes(branch)
          print(f"Theme {theme_to_download} downloaded and verified successfully!")
          self.params_memory.put("WheelDownloadProgress", "Downloaded!")
          self.params_memory.remove("WheelToDownload")
          return
      except Exception as e:
        continue

    self.handle_verification_failure(theme_to_download, theme_path, theme_url)

  @staticmethod
  def fetch_files(url):
    try:
      response = requests.get(url)
      response.raise_for_status()
      file_names = re.findall(r'href="[^"]*\/blob\/[^"]*\/([^"]*)"', response.text)
      file_names = [name for name in file_names if name.lower() != "license"]
      return file_names
    except requests.RequestException:
      return []

  def update_theme_params(self, theme_files):
    wheel_dir = os.path.join(THEME_SAVE_PATH, "steering_wheels")
    available_files = os.listdir(wheel_dir)
    available_wheels = [wheel.replace('_', ' ').split('.')[0].title() for wheel in available_files if wheel != "img_chffr_wheel.png"]
    available_wheels.extend(["Stock", "None"])
    available_wheels = sorted(set(available_wheels))
    self.params.put("AvailableWheels", ','.join(available_wheels))

    downloadable_wheels = [wheel.replace('_', ' ').split('.')[0].title() for wheel in theme_files if wheel.replace('_', ' ').split('.')[0].title() not in available_wheels]
    downloadable_wheels = sorted(set(downloadable_wheels))
    self.params.put("DownloadableWheels", ','.join(downloadable_wheels))
    print("Wheels list updated successfully.")

  def update_themes(self, branch_name):
    repo_url = get_repository_url()
    if repo_url is None:
      self.handle_error(None, "Github and Gitlab are offline...", "Github and Gitlab are offline...")
      return

    theme_files = self.fetch_files(f"https://github.com/FrogAi/FrogPilot-Resources/tree/{branch_name}")
    if not theme_files:
      theme_files = self.fetch_files(f"https://gitlab.com/FrogAi/FrogPilot-Resources/-/tree/{branch_name}")

    if theme_files:
      self.update_theme_params(theme_files)
    else:
      print("Failed to update themes from both GitHub and GitLab.")
