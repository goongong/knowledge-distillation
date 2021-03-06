import numpy as np

def softmax_with_temp(x):
    Temp = 1.0
    e_x = np.exp((x - x.max(axis=1, keepdims=True))/Temp)
    out = e_x / e_x.sum(axis=1, keepdims=True)
    return out
