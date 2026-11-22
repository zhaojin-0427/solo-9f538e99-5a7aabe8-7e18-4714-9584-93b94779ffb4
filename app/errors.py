"""共享的输入校验异常。"""


class ValidationError(Exception):
    """输入不合法：本次操作失败，原有数据继续有效。"""
