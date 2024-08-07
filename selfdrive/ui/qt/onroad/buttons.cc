#include "selfdrive/ui/qt/onroad/buttons.h"

#include <QPainter>

#include "selfdrive/ui/qt/util.h"

void drawIcon(QPainter &p, const QPoint &center, const QPixmap &img, const QBrush &bg, float opacity, const int angle) {
  p.setRenderHint(QPainter::Antialiasing);
  p.setOpacity(1.0);  // bg dictates opacity of ellipse
  p.setPen(Qt::NoPen);
  p.setBrush(bg);
  p.drawEllipse(center, btn_size / 2, btn_size / 2);
  p.save();
  p.translate(center);
  p.rotate(angle);
  p.setOpacity(opacity);
  p.drawPixmap(-QPoint(img.width() / 2, img.height() / 2), img);
  p.setOpacity(1.0);
  p.restore();
}

// ExperimentalButton
ExperimentalButton::ExperimentalButton(QWidget *parent) : experimental_mode(false), engageable(false), QPushButton(parent) {
  setFixedSize(btn_size, btn_size + 10);

  engage_img = loadPixmap("../assets/img_chffr_wheel.png", {img_size, img_size});
  experimental_img = loadPixmap("../assets/img_experimental.svg", {img_size, img_size});
  QObject::connect(this, &QPushButton::clicked, this, &ExperimentalButton::changeMode);

  // FrogPilot variables
  wheelGifPath = "../frogpilot/assets/active_theme/images/wheel.gif";
  wheelPngPath = "../frogpilot/assets/active_theme/images/wheel.png";

  gifFile.setFileName(wheelGifPath);
  pngFile.setFileName(wheelPngPath);

  gifLabel = new QLabel(this);
  gifLabel->setScaledContents(true);
  updateIcon();

  status_color_map = {
    {"default", QColor(0, 0, 0, 166)},
    {"always_on_lateral_active", bg_colors[STATUS_ALWAYS_ON_LATERAL_ACTIVE]},
    {"conditional_overridden", bg_colors[STATUS_CONDITIONAL_OVERRIDDEN]},
    {"experimental_mode_active", bg_colors[STATUS_EXPERIMENTAL_MODE_ACTIVE]},
    {"navigation_active", bg_colors[STATUS_NAVIGATION_ACTIVE]},
    {"traffic_mode_active", bg_colors[STATUS_TRAFFIC_MODE_ACTIVE]}
  };
}

void ExperimentalButton::changeMode() {
  const auto cp = (*uiState()->sm)["carParams"].getCarParams();
  bool can_change = hasLongitudinalControl(cp) && params.getBool("ExperimentalModeConfirmed");
  if (can_change) {
    if (conditionalExperimental) {
      int override_value = (conditionalStatus >= 1 && conditionalStatus <= 6) ? 0 : conditionalStatus >= 7 ? 5 : 6;
      paramsMemory.putInt("CEStatus", override_value);
    } else {
      params.putBool("ExperimentalMode", !experimental_mode);
    }
  }
}

void ExperimentalButton::updateState(const UIState &s, bool leadInfo) {
  const auto cs = (*s.sm)["controlsState"].getControlsState();
  bool eng = cs.getEngageable() || cs.getEnabled() || alwaysOnLateralActive;
  if ((cs.getExperimentalMode() != experimental_mode) || (eng != engageable)) {
    engageable = eng;
    experimental_mode = cs.getExperimentalMode();
    update();
  }

  // FrogPilot variables
  const UIScene &scene = s.scene;

  alwaysOnLateralActive = scene.always_on_lateral_active;
  bigMap = scene.big_map;
  conditionalExperimental = scene.conditional_experimental;
  conditionalStatus = scene.conditional_status;
  mapOpen = scene.map_open;
  navigateOnOpenpilot = scene.navigate_on_openpilot;
  rotatingWheel = scene.rotating_wheel;
  trafficModeActive = scene.traffic_mode_active;
  y_offset = leadInfo ? 10 : 0;

  if (rotatingWheel && steeringAngleDeg != scene.steering_angle_deg) {
    steeringAngleDeg = scene.steering_angle_deg;
    update();
  } else if (!rotatingWheel) {
    steeringAngleDeg = 0;
  }
}

void ExperimentalButton::updateBackgroundColor() {
  if (!imageEmpty && !isDown() && engageable) {
    if (alwaysOnLateralActive) {
      background_color = status_color_map["always_on_lateral_active"];
    } else if (conditionalStatus == 1 || conditionalStatus == 3 || conditionalStatus == 5) {
      background_color = status_color_map["conditional_overridden"];
    } else if (experimental_mode) {
      background_color = status_color_map["experimental_mode_active"];
    } else if (navigateOnOpenpilot) {
      background_color = status_color_map["navigation_active"];
    } else if (trafficModeActive) {
      background_color = status_color_map["traffic_mode_active"];
    } else {
      background_color = status_color_map["default"];
    }
  } else {
    background_color = status_color_map["default"];
  }
}

void ExperimentalButton::updateIcon() {
  if (gifFile.exists()) {
    if (!movie) {
      movie = new QMovie(wheelGifPath);
      gifLabel->setMovie(movie);
    } else {
      movie->stop();
      movie->setFileName(wheelGifPath);
    }
    movie->start();

    gifLabel->show();
    gifLabel->resize(img_size, img_size);
    gifLabel->move((btn_size - img_size) / 2, (btn_size - img_size) / 2 + y_offset);

    imageEmpty = false;
    useGif = true;
  } else if (pngFile.exists()) {
    img = loadPixmap(wheelPngPath, {img_size, img_size});
    gifLabel->hide();
    imageEmpty = false;
    useGif = false;
  } else {
    imageEmpty = true;
    useGif = false;
  }

  update();
}

void ExperimentalButton::paintEvent(QPaintEvent *event) {
  if (bigMap && mapOpen || imageEmpty || useGif) {
    return;
  }

  QPainter p(this);
  updateBackgroundColor();
  drawIcon(p, QPoint(btn_size / 2, btn_size / 2 + y_offset), img, background_color, (isDown() || !engageable) ? 0.6 : 1.0, steeringAngleDeg);
}

// MapSettingsButton
MapSettingsButton::MapSettingsButton(QWidget *parent) : QPushButton(parent) {
  setFixedSize(btn_size, btn_size + 20);
  settings_img = loadPixmap("../assets/navigation/icon_directions_outlined.svg", {img_size, img_size});

  // hidden by default, made visible if map is created (has prime or mapbox token)
  setVisible(false);
  setEnabled(false);
}

void MapSettingsButton::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  drawIcon(p, QPoint(btn_size / 2, btn_size / 2), settings_img, QColor(0, 0, 0, 166), isDown() ? 0.6 : 1.0);
}

// FrogPilot buttons

// DistanceButton
DistanceButton::DistanceButton(QWidget *parent) : QPushButton(parent) {
  setFixedSize(btn_size * 1.5, btn_size * 1.5);

  connect(this, &QPushButton::pressed, this, &DistanceButton::buttonPressed);
  connect(this, &QPushButton::released, this, &DistanceButton::buttonReleased);
}

void DistanceButton::buttonPressed() {
  paramsMemory.putBool("OnroadDistanceButtonPressed", true);
}

void DistanceButton::buttonReleased() {
  paramsMemory.putBool("OnroadDistanceButtonPressed", false);
}

void DistanceButton::updateState(const UIScene &scene) {
  bool stateChanged = (trafficModeActive != scene.traffic_mode_active) ||
                      (personality != static_cast<int>(scene.personality) + 1 && !trafficModeActive);

  if (stateChanged) {
    personality = static_cast<int>(scene.personality) + 1;
    trafficModeActive = scene.traffic_mode_active;

    int profile = trafficModeActive ? 0 : personality;
    std::tie(profileImage, profileText) = (scene.use_kaofui_icons ? profileDataKaofui : profileData)[profile];

    transitionTimer.restart();
    update();
  } else if (transitionTimer.isValid()) {
    update();
  } else {
    return;
  }
}

void DistanceButton::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setRenderHints(QPainter::Antialiasing | QPainter::TextAntialiasing);

  int elapsed = transitionTimer.elapsed();
  qreal textOpacity = qBound(0.0, 1.0 - ((elapsed - 3000.0) / 1000.0), 1.0);
  qreal imageOpacity = 1.0 - textOpacity;

  p.setOpacity(textOpacity);
  p.setFont(InterFont(40, QFont::Bold));
  p.setPen(Qt::white);
  QRect textRect(-25, 0, width(), height() + btn_size / 2);
  p.drawText(textRect, Qt::AlignCenter, profileText);

  drawIcon(p, QPoint((btn_size / 2) * 1.25, btn_size), profileImage, Qt::transparent, imageOpacity);
}
