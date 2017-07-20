from threading import current_thread


def get_thread_ident():
    return current_thread().ident

