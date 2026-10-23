"""Sparse NumPy STFT/mel frontend using constants from the pinned OV model.

Use the exported (FP16-rounded) window, filterbanks, preemphasis, log floor
and normalization epsilon. Floating-point reductions can differ from OV;
exact digital silence is explicitly normalized to zero.
"""
import numpy as np
from model_io import read_ir


class MelFrontend:
    def __init__(self, core, path):
        model = read_ir(core,path)
        constants = {node.get_friendly_name(): node.get_data().copy()
                     for node in model.get_ordered_ops() if node.get_type_name() == 'Constant'}
        bank = constants['self.filterbanks_compressed'].astype(np.float32)[0]
        self.window = np.pad(constants['self.window_compressed'].astype(np.float32), (56, 56))
        rows, self.bins = np.nonzero(bank)
        self.weights = bank[rows, self.bins]
        self.starts = np.flatnonzero(np.r_[True, rows[1:] != rows[:-1]])
        if bank.shape != (128, 257) or len(self.starts) != 128 or self.window.shape != (512,):
            raise RuntimeError('Unexpected pinned frontend constants')
        self.preemphasis = float(constants['Constant_59620_compressed'].reshape(-1)[0])
        self.log_floor = float(constants['Constant_59623_compressed'].reshape(-1)[0])
        self.std_epsilon = float(constants['Constant_59624_compressed'].reshape(-1)[0])

    def __call__(self, audio):
        x = np.asarray(audio, dtype=np.float32).reshape(-1)
        if np.max(np.abs(x)) < 1e-10:
            return np.zeros((1, 128, len(x)//160+1), np.float32)
        signal = x.copy()
        signal[1:] -= self.preemphasis * x[:-1]
        padded = np.pad(signal, (256, 256), mode='reflect')
        frames = np.lib.stride_tricks.sliding_window_view(padded, 512)[::160]
        fft = np.fft.rfft(frames * self.window, axis=-1)
        power = (fft.real**2 + fft.imag**2).T
        # Exported triangular filterbanks are sparse. Avoid a dense BLAS thread
        # pool and its scheduling/spinning cost for this small matrix product.
        mel = np.add.reduceat(power[self.bins] * self.weights[:, None], self.starts, axis=0)
        logmel = np.log(mel + self.log_floor)
        centered = logmel - logmel.mean(axis=1, keepdims=True)
        scale = logmel.std(axis=1, ddof=1, keepdims=True) + self.std_epsilon
        return np.ascontiguousarray((centered / scale)[None, :, :], dtype=np.float32)
