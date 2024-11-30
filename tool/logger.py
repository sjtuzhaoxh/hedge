import logging
import time


def get_logger(
    name: str,
    level=logging.DEBUG,
    fmt='%(asctime)s - [%(name)s] %(message)s',
):
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 检查是否已经有处理器，以避免重复添加
    if not logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        formatter = logging.Formatter(fmt)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger

def throttled_logger():
    last_log_time = 0

    def log(message):
        nonlocal last_log_time
        current_time = time.time()
        if current_time - last_log_time >= 1:
            print(message)
            last_log_time = current_time

    return log


# 节流版日志
tlog = throttled_logger()
