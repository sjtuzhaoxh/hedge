import time


def time_s():
    """秒级时间戳"""
    return int(time.time())


def time_ms():
    """毫秒级时间戳"""
    return int(round(time.time() * 1000))

