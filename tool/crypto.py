import hashlib


def md5(data: str) -> str:
    hash = hashlib.md5()
    hash.update(data.encode('utf-8'))
    return hash.hexdigest()


def sha256(data: str) -> str:
    hash = hashlib.sha256()
    hash.update(data.encode('utf-8'))
    return hash.hexdigest()
