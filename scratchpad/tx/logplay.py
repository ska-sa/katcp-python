import logging

from logwrapper import LogWrapper

logger = logging.getLogger()

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(filename)s(%(lineno)d): "
                    "%(message)s")

lw = LogWrapper(logger)

lw.info('Wrapped well, this is interesting')
