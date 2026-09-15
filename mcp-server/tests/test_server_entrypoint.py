# 서버 기동 블록이 도구 정의를 가로막지 않는지 검증한다.
import pathlib
import re

SERVER = pathlib.Path(__file__).resolve().parents[1] / "server.py"


def _lines():
    return SERVER.read_text().split("\n")


def test_server_entrypoint_is_last():
    """`if __name__ == "__main__":` 뒤에는 @mcp.tool() 이 하나도 없어야 한다.

    uvicorn.run 은 블로킹이라 그 줄에서 실행이 멈춘다. 기동 블록을 중간에 두면
    아래의 도구 정의가 **아예 실행되지 않아 조용히 사라진다** —
    실측(2026-09-15): 블록이 514줄에 있어서 뒤에 정의된 결함 5종·통계 4종이
    tools/list 에 나오지 않았다(정의 34개 중 25개만 서비스).

    import 로 열면 이 블록을 건너뛰므로 모든 도구가 보인다. 그래서 모듈을
    import 해 세는 시험으로는 절대 안 잡힌다 — 소스 순서를 봐야 한다.
    """
    lines = _lines()
    mains = [i for i, l in enumerate(lines) if l.startswith('if __name__ == "__main__":')]
    assert len(mains) == 1, f"__main__ 블록이 {len(mains)}개다"
    main_i = mains[0]

    after = [(i + 1, lines[i + 1] if i + 1 < len(lines) else "")
             for i, l in enumerate(lines) if l.strip() == "@mcp.tool()" and i > main_i]
    assert not after, (
        f"__main__({main_i + 1}줄) 뒤에 @mcp.tool() 이 {len(after)}개 있다 — "
        f"{[n for n, _ in after]}줄. 이 도구들은 서버에서 등록되지 않는다. "
        "기동 블록을 파일 맨 아래로 옮겨라")


def test_all_defined_tools_are_before_entrypoint():
    """정의된 도구 수와 기동 전에 정의된 도구 수가 같아야 한다."""
    src = SERVER.read_text()
    defined = re.findall(r"@mcp\.tool\(\)\s*\nasync def (\w+)", src)
    head = src.split('if __name__ == "__main__":')[0]
    before = re.findall(r"@mcp\.tool\(\)\s*\nasync def (\w+)", head)
    missing = [d for d in defined if d not in before]
    assert not missing, f"기동 블록 뒤에 묻힌 도구: {missing}"
    assert len(defined) >= 34, f"도구가 {len(defined)}개뿐이다 — 회귀 의심"
