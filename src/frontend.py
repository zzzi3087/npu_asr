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


class V3MelFrontend:
    """FFT equivalent of the pinned v3 convolutional STFT graph.

    Preserve its left-padded preemphasis, 400-sample constant-padded window,
    exported rounded coefficients. Use the export's full 15 s length: varying
    the mask length can suppress speech on NPU. Compute FFT only for the
    nonzero prefix; fill the remaining known-silent spectrum from a constant.
    """
    def __init__(self,core,path):
        model=read_ir(core,path)
        constants={node.get_friendly_name():node.get_data().copy() for node in model.get_ordered_ops() if node.get_type_name()=='Constant'}
        basis=constants['aten::to/Convert_3_compressed'].astype(np.float32)
        bank=constants['aten::to/Convert_2_compressed'].astype(np.float32)
        self.kernel=constants['self.preemph_kernel_compressed'].astype(np.float32).reshape(-1)
        if basis.shape!=(514,1,400) or bank.shape!=(128,257) or len(self.kernel)!=2:raise ValueError('v3 前处理配置不匹配')
        self.window=basis[0,0]
        rows,self.bins=np.nonzero(bank);self.weights=bank[rows,self.bins]
        self.starts=np.flatnonzero(np.r_[True,rows[1:]!=rows[:-1]])
        self.rows=rows[self.starts]
        self.log_floor=float(constants['Constant_36660_compressed'].reshape(-1)[0])
        self.std_epsilon=float(constants['Constant_36663_compressed'].reshape(-1)[0])

    @staticmethod
    def length(samples):return 1498

    def __call__(self,audio):
        x=np.asarray(audio,dtype=np.float32).reshape(-1);count=1501
        if len(x)>240000:raise ValueError('v3 音频超过 15 秒窗口')
        if np.max(np.abs(x))<1e-10:return np.zeros((1,128,count),np.float32)
        # Include preemphasis across the speech-to-padding edge and every
        # overlapping 400-sample window. Later frames have exactly zero power.
        work_samples=min(240000,((len(x)+559)//160)*160)
        x=np.pad(x,(0,work_samples-len(x)))
        signal=x*self.kernel[1];signal[1:]+=self.kernel[0]*x[:-1]
        frames=np.lib.stride_tricks.sliding_window_view(np.pad(signal,(200,200)),400)[::160]
        fft=np.fft.rfft(frames*self.window,n=512,axis=-1)
        power=(fft.real**2+fft.imag**2).T
        mel=np.zeros((128,power.shape[1]),np.float32)
        mel[self.rows]=np.add.reduceat(power[self.bins]*self.weights[:,None],self.starts,axis=0)
        logged=np.full((128,count),np.log(np.float32(self.log_floor)),np.float32)
        logged[:,:mel.shape[1]]=np.log(mel+self.log_floor);valid=self.length(len(x))
        selected=logged[:,:valid];center=selected.mean(axis=1,keepdims=True)
        scale=selected.std(axis=1,ddof=1 if valid>1 else 0,keepdims=True)+self.std_epsilon
        result=np.zeros_like(logged);result[:,:valid]=(selected-center)/scale
        return np.ascontiguousarray(result[None,:,:],dtype=np.float32)
