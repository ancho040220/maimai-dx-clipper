"""설치 후 정리 — 안 쓰는 Google API 정의 파일을 지운다.

google-api-python-client 는 580개 Google API의 정의 JSON을 함께 설치하는데
(93MB) 이 프로그램이 쓰는 것은 youtube.v3.json 하나뿐이다.

지워도 동작에 영향이 없다는 것은 build("youtube","v3") 로 확인했다.
pip 으로 패키지를 다시 설치하면 되돌아오므로, setup.bat 이 매번 실행한다.
"""
import sys


KEEP = {"youtube.v3.json"}


def main() -> int:
    try:
        import importlib.util as iu
        from pathlib import Path
        spec = iu.find_spec("googleapiclient")
        if spec is None or not spec.origin:
            print("  google-api-python-client not installed - skipped")
            return 0
        docs = Path(spec.origin).parent / "discovery_cache" / "documents"
        if not docs.is_dir():
            print("  discovery cache not found - skipped")
            return 0

        targets = [f for f in docs.glob("*.json") if f.name not in KEEP]
        if not targets:
            print("  already pruned")
            return 0

        freed = 0
        removed = 0
        for f in targets:
            try:
                size = f.stat().st_size
                f.unlink()
                freed += size
                removed += 1
            except Exception:
                pass
        print(f"  removed {removed} unused API definitions, freed {freed / 1024 ** 2:.0f} MB")
        return 0
    except Exception as e:
        # 정리 실패가 설치를 막아서는 안 된다
        print(f"  skipped ({type(e).__name__})")
        return 0


if __name__ == "__main__":
    sys.exit(main())
