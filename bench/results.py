import math
import pylab

class Result(object):
    def __init__(self, scenario, interpreter, lib, result):
        self.scenario = scenario
        self.interpreter = interpreter
        self.lib = lib
        self.result = result

results = [Result('scenario1', 'cpython 2.6.5', 'katcp',
                  [(2875, 1), (2741, 1), (2591, 1), (2730, 1), (2808, 1), (2547, 1), (1077, 2), (1013, 2), (1218, 2), (1017, 2), (1228, 2), (1127, 2), (1151, 3), (1039, 3), (1196, 3), (1603, 3), (951, 3), (1207, 3), (1069, 4), (1086, 4), (1001, 4), (1291, 4), (1188, 4), (1096, 4), (1028, 5), (879, 5), (894, 5), (879, 5), (810, 5), (999, 5)]),
           Result('scenario1', 'cpython 2.6.5', 'txkatcp',
                  [(2246, 1), (2640, 1), (2292, 1), (2238, 1), (2285, 1), (2274, 1), (2173, 2), (1860, 2), (2217, 2), (2308, 2), (2234, 2), (2322, 2), (1983, 3), (2007, 3), (2010, 3), (1989, 3), (2023, 3), (2081, 3), (1952, 4), (1951, 4), (1796, 4), (1811, 4), (1959, 4), (1851, 4), (1780, 5), (1895, 5), (1821, 5), (1822, 5), (1715, 5), (1775, 5)]),
           Result('scenario1', 'pypy-c-78182-jit32', 'katcp',
                  [(3658, 1), (3658, 1), (3657, 1), (3536, 1), (3659, 1), (3658, 1), (5426, 2), (4855, 2), (4620, 2), (5422, 2), (4503, 2), (4886, 2), (1768, 3), (1506, 3), (1636, 3), (1627, 3), (1360, 3), (1856, 3), (1545, 4), (1716, 4), (1279, 4), (1512, 4), (1849, 4), (1673, 4), (1328, 5), (1552, 5), (1851, 5), (1766, 5), (1336, 5), (1493, 5)]),
           Result('scenario1', 'pypy-c-78182-jit32', 'txkatcp',
                  [(2443, 1), (2444, 1), (2447, 1), (2440, 1), (2439, 1), (2438, 1), (4994, 2), (4860, 2), (3626, 2), (3808, 2), (3780, 2), (3580, 2), (3704, 3), (3477, 3), (3805, 3), (3693, 3), (3784, 3), (3740, 3), (3234, 4), (3143, 4), (3223, 4), (2942, 4), (3223, 4), (3203, 4), (2146, 5), (2338, 5), (2645, 5), (2523, 5), (2600, 5), (2193, 5)])]

def compute_all(all):
    colors = ['green', 'red', 'yellow', 'brown']
    
    for ii, result in enumerate(all):
        d = {}
        max_clients = max([i for v, i in result.result])
        res = [None] * max_clients
        errors = [None] * max_clients
        for no, clients in result.result:
            d.setdefault(clients, []).append(no)
        for clients, v in d.items():
            s = sum(v)/len(v)
            df = math.sqrt(sum([(s - i)*(s - i) for i in v]) / (len(v) - 1))
            res[clients - 1] = s
            errors[clients - 1] = df
        left = [i + ii*0.2 for i in range(1, max_clients + 1)]
        pylab.bar(left, res, yerr=errors, color=colors[ii],
                  width=0.2)
    pylab.legend(["%s, %s" % (result.lib, result.interpreter)
                  for result in all])
    pylab.show()

if __name__ == '__main__':
    compute_all(results)
