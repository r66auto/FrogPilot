import os
import requests

from openpilot.selfdrive.frogpilot.controls.lib.frogpilot_functions import is_url_pingable

GITHUB_URL = "https://raw.githubusercontent.com/FrogAi/FrogPilot-Resources/"
GITLAB_URL = "https://gitlab.com/FrogAi/FrogPilot-Resources/-/raw/"

def get_remote_file_size(url):
  try:
    response = requests.head(url, timeout=5)
    response.raise_for_status()
    return int(response.headers.get('Content-Length', 0))
  except requests.RequestException as e:
    print(f"Error fetching file size: {e}")
    return None

def get_repository_url():
  if is_url_pingable("https://github.com"):
    return GITHUB_URL
  if is_url_pingable("https://gitlab.com"):
    return GITLAB_URL
  return None

def verify_download(file_path, url):
  if not os.path.exists(file_path):
    return False

  remote_file_size = get_remote_file_size(url)
  if remote_file_size is None:
    return False

  return remote_file_size == os.path.getsize(file_path)
