import numpy as np

def compute_bf(csi_phase, method="mrt"):
    """
    根据 CSI 相位计算 Beamforming 权重，目前支持 MRT。
    输出为标量：如果 BF 是向量，则返回第一个元素。
    """
    phase = np.asarray(csi_phase)

    if method.lower() == "mrt":
        # MRT: w = conj(h) / ||h||
        h = np.exp(1j * phase)
        w = np.conj(h)
        norm = np.linalg.norm(w)
        if norm == 0:
            raise ValueError("Channel norm is zero, cannot compute MRT weight.")
        bf = w / norm
    else:
        raise NotImplementedError(f"Unknown BF method: {method}")

    # 返回标量（兼容向量情况）
    bf = np.asarray(bf)
    if bf.ndim == 0:
        return bf
    return np.ravel(bf)[0]
