"""GPU 사용 가능 여부 조회.

기존에는 torch.cuda.is_available() 을 썼지만 torch는 CUDA 빌드 기준 4GB가 넘고,
이 프로젝트에서 torch가 실제로 하던 일은 YOLO 추론뿐이었다. 추론을 ONNX Runtime
으로 옮기면서 GPU 판별도 nvidia-smi 와 onnxruntime provider 로 대체한다.

- ffmpeg 하드웨어 가속·워커 수 판단  → has_nvidia() / vram_gb()
- 모델 추론 장치                      → onnx_providers()
"""
import subprocess
import sys
from typing import Optional

_nvidia:  Optional[bool]  = None
_vram_gb: Optional[float] = None


def _nvidia_smi(args: list) -> Optional[str]:
    kwargs = {"capture_output": True, "text": True, "timeout": 5}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000        # CREATE_NO_WINDOW
    try:
        r = subprocess.run(["nvidia-smi"] + args, **kwargs)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def has_nvidia() -> bool:
    """NVIDIA GPU 존재 여부. 프로세스마다 한 번만 조회한다."""
    global _nvidia
    if _nvidia is None:
        _nvidia = _nvidia_smi(["-L"]) is not None
    return _nvidia


def vram_gb() -> float:
    """총 VRAM(GB). 조회 실패 시 0.0."""
    global _vram_gb
    if _vram_gb is None:
        _vram_gb = 0.0
        out = _nvidia_smi(["--query-gpu=memory.total", "--format=csv,noheader,nounits"])
        if out:
            try:
                _vram_gb = float(out.strip().splitlines()[0]) / 1024
            except Exception:
                pass
    return _vram_gb


def onnx_providers() -> list:
    """설치된 onnxruntime에서 쓸 수 있는 실행 provider를 빠른 순으로 반환.

    onnxruntime(CPU 전용) / onnxruntime-gpu(CUDA) / onnxruntime-directml 중
    무엇이 깔려 있든 동작한다.
    """
    import onnxruntime as ort
    avail = ort.get_available_providers()
    for p in ("CUDAExecutionProvider", "DmlExecutionProvider"):
        if p in avail:
            return [p, "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def onnx_uses_gpu() -> bool:
    """모델 추론이 GPU에서 도는지."""
    try:
        return onnx_providers()[0] != "CPUExecutionProvider"
    except Exception:
        return False
