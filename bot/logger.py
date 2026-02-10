import logging
import os
from logging.handlers import TimedRotatingFileHandler


_ROOT_LOGGER = logging.getLogger()
if not _ROOT_LOGGER.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

_LOGGER = logging.getLogger("futures_signals")
_ACCEPTED_LOGGER = logging.getLogger("futures_signals.accepted")

LOG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logs"))
LOG_FILE = os.path.join(LOG_DIR, "signals.log")

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR, exist_ok=True)

_file_handler_exists = any(
    isinstance(h, TimedRotatingFileHandler) and getattr(h, "baseFilename", "") == LOG_FILE
    for h in _LOGGER.handlers
)
if not _file_handler_exists:
    file_handler = TimedRotatingFileHandler(
        LOG_FILE,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )
    _LOGGER.addHandler(file_handler)

ACCEPTED_LOG_FILE = os.path.join(LOG_DIR, "accepted_signals.log")
_accepted_handler_exists = any(
    isinstance(h, TimedRotatingFileHandler) and getattr(h, "baseFilename", "") == ACCEPTED_LOG_FILE
    for h in _ACCEPTED_LOGGER.handlers
)
if not _accepted_handler_exists:
    accepted_handler = TimedRotatingFileHandler(
        ACCEPTED_LOG_FILE,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    accepted_handler.suffix = "%Y-%m-%d"
    accepted_handler.setLevel(logging.INFO)
    accepted_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )
    _ACCEPTED_LOGGER.addHandler(accepted_handler)


def log_decision(
    asset,
    direction,
    market_state,
    reason,
    zone_resolution=None,
    zone_reason=None,
    zone_state=None,
    zone_confidence=None,
    zone_confirmation=None,
    zone_source=None,
    approach_direction=None,
    indicator_score=None,
    indicator_bias=None,
):
    state_value = getattr(market_state, "value", market_state)
    zone_value = getattr(zone_resolution, "value", zone_resolution) if zone_resolution else "NONE"
    zone_detail = zone_reason or ""
    zone_state_value = zone_state or "N/A"
    zone_conf_value = "N/A" if zone_confidence is None else str(zone_confidence)
    zone_conf_flag = "N/A" if zone_confirmation is None else str(zone_confirmation)
    indicator_score_value = "N/A" if indicator_score is None else str(indicator_score)
    indicator_bias_value = indicator_bias or "N/A"
    zone_source_value = zone_source or "N/A"
    approach_value = approach_direction or "N/A"
    log_line = (
        "[%s] %s | STATE=%s | RESULT=%s | ZONE=%s | ZONE_REASON=%s "
        "| ZONE_STATE=%s | ZONE_CONFIDENCE=%s | CONFIRMATION=%s | ZONE_SOURCE=%s | APPROACH=%s "
        "| INDICATOR_SCORE=%s | INDICATOR_BIAS=%s"
    )
    _LOGGER.info(
        log_line,
        asset,
        direction,
        state_value,
        reason,
        zone_value,
        zone_detail,
        zone_state_value,
        zone_conf_value,
        zone_conf_flag,
        zone_source_value,
        approach_value,
        indicator_score_value,
        indicator_bias_value,
    )
    if reason == "SIGNAL_ACCEPTED":
        _ACCEPTED_LOGGER.info(
            log_line,
            asset,
            direction,
            state_value,
            reason,
            zone_value,
            zone_detail,
            zone_state_value,
            zone_conf_value,
            zone_conf_flag,
            zone_source_value,
            approach_value,
            indicator_score_value,
            indicator_bias_value,
        )
