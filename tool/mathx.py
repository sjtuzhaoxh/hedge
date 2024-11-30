import math


def floor(number, decimals):
    """float数据向下取整"""
    if number == 0: return 0
    factor = 10**decimals
    n = math.floor(number * factor)
    if n == 0: return 0
    return n / factor


def ceil(number, decimals):
    """float数据向上取整"""
    if number == 0: return 0
    factor = 10**decimals
    n = math.ceil(number * factor)
    if n == 0: return 0
    return n / factor


def calc_spread(high, low):
    """计算价差"""
    return (high - low) / ((high + low) / 2)


def prec(f: float):
    """获取浮点数的精度"""
    # 转换为字符串
    str_f = str(f)

    # 如果是科学计数法，则转换为小数点形式
    if 'e' in str_f or 'E' in str_f:
        str_f = format(f, 'f')

    # 找到小数点位置并处理小数点后的部分
    if '.' in str_f:
        # 去除小数点后的多余零
        decimal_part = str_f.split('.')[1].rstrip('0')
        if decimal_part:
            return len(decimal_part)
        else:
            return 0  # 如果小数部分是0，精度为0
    else:
        return 0  # 如果没有小数部分，精度为0


if __name__ == '__main__':
    print(ceil(149.67, 5))
    print(str(149.67))
