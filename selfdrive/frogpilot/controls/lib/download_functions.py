import os
import requests

from openpilot.selfdrive.frogpilot.controls.lib.frogpilot_functions import delete_file, is_url_pingable

GITHUB_URL = "https://raw.githubusercontent.com/FrogAi/FrogPilot-Resources/"
GITLAB_URL = "https://gitlab.com/FrogAi/FrogPilot-Resources/-/raw/"

def download_file(cancel_param, destination, progress_param, url, theme_param, params_memory):
  try:
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with requests.get(url, stream=True, timeout=5) as response:
      response.raise_for_status()
      total_size = get_remote_file_size(url)
      downloaded_size = 0

      with open(destination, 'wb') as file:
        for chunk in response.iter_content(chunk_size=8192):
          if params_memory.get_bool(cancel_param):
            handle_error(destination, "Download cancelled...", "Download cancelled...", theme_param, progress_param, params_memory)
            return

          if chunk:
            file.write(chunk)
            downloaded_size += len(chunk)
            progress = (downloaded_size / total_size) * 100

            if progress != 100:
              params_memory.put(progress_param, f"{progress:.0f}%")
            else:
              params_memory.put(progress_param, "Verifying authenticity...")

  except requests.HTTPError as http_error:
    if http_error.response.status_code == 404:
      return False
    handle_error(destination, f"Failed: Server error ({http_error.response.status_code})", http_error, theme_param, progress_param, params_memory)
  except requests.ConnectionError as connection_error:
    handle_error(destination, "Failed: Connection dropped...", connection_error, theme_param, progress_param, params_memory)
  except requests.Timeout as timeout_error:
    handle_error(destination, "Failed: Download timed out...", timeout_error, theme_param, progress_param, params_memory)
  except requests.RequestException as request_error:
    handle_error(destination, "Failed: Network request error. Check connection.", request_error, theme_param, progress_param, params_memory)
  except Exception as e:
    handle_error(destination, "Failed: Unexpected error.", e, theme_param, progress_param, params_memory)

def handle_error(destination, error_message, error, download_param, progress_param, params_memory):
  print(f"Error occurred: {error}")
  params_memory.put(progress_param, error_message)
  params_memory.remove(download_param)
  if destination:
    delete_file(destination)

def get_remote_file_size(url):
  headers = {'Accept-Encoding': 'identity'}
  response = requests.head(url, headers=headers, timeout=5)
  response.raise_for_status()

  content_length = response.headers.get('Content-Length', None)
  if content_length is None:
    return 0
  return int(content_length)

def get_repository_url():
  if is_url_pingable("https://github.com"):
    return GITHUB_URL
  if is_url_pingable("https://gitlab.com"):
    return GITLAB_URL
  return None

def link_valid(url):
  response = requests.head(url)
  return response.status_code == 200

def verify_download(file_path, url):
  if not os.path.exists(file_path):
    print(f"File not found: {file_path}")
    return False

  remote_file_size = get_remote_file_size(url)
  if remote_file_size is None:
    return False

  return remote_file_size == os.path.getsize(file_path)
