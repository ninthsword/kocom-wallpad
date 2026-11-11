# 개발 및 검증

Python 3.14.7, uv 0.12.5, Node.js 24를 사용합니다. 해시 잠금된 개발 의존성에는
Home Assistant 2026.8.0, serialx 1.11.0, Ruff 0.16.4가 포함됩니다.
Pyright 1.1.413은 `devtools/pyright` 아래에 격리되어 있습니다. 런타임 의존성은 `manifest.json`의 `serialx>=1.11.0`이며, 개발 검증은 잠금 파일의 버전을 사용합니다.
Python 3.11 구문 호환성은 계속 유지되며, 지원하는 Home Assistant 최소 버전은
2026.8.0입니다.

```sh
python3 -m pip install uv==0.12.5
uv venv --python 3.14.7 .venv
uv pip sync --python .venv/bin/python --require-hashes requirements-dev.lock
npm ci --prefix devtools/pyright --ignore-scripts --no-audit --no-fund
devtools/pyright/node_modules/.bin/pyright --project pyrightconfig.json --pythonpath .venv/bin/python --outputjson
.venv/bin/ruff check --no-cache custom_components tests
.venv/bin/python -B -m unittest discover -s tests -v
```

Pyright는 로그북 설명 모듈 `logbook.py`를 포함한 15개 통합구성요소 모듈과 오프라인 회귀 테스트 모듈 전체를
실제 개발 의존성을 기준으로 검사합니다. Config-flow 테스트와 `tests/test_climate_core_compat.py`는 프로토콜 shim과 분리된
새 서브프로세스에서 실제 Home Assistant로 실행됩니다. 나머지 테스트는 좁은 범위의 Home Assistant shim과 가짜
트랜스포트를 사용해 네트워크나 실제 장치 접근 없이 패킷 바이트, 확인 응답, 재연결,
종료 동작을 검증합니다. 또한 `DeviceState` 생성자, 데이터클래스 필드,
직렬화, 초기에 존재하지 않는 동적 메타데이터도 함께 보호합니다.

의존성을 의도적으로 갱신할 때는 `requirements-dev.in`과 해시로 검증되는
`requirements-dev.lock`을 항상 함께 갱신하세요. CI는 이 검사들을 실행하고
런타임 소스를 Python 3.11 구문 규칙으로 파싱합니다. 이 검사들은 통합구성요소를
Home Assistant에 설치하거나 서비스·장치를 동작시키지 않습니다.


## Home Assistant 2026.9 호환성 검증

기존 2026.8 최소 버전 검증은 유지합니다. 2026.9.2 검증은 Python 3.14.7과
별도 해시 잠금 파일을 사용하며, 실제 서비스나 장치에 연결하지 않습니다.
`uv==0.12.5`, Node.js 24와 저장소의 잠긴 Pyright 의존성을 사용합니다.

```sh
uv venv --python 3.14.7 .venv-ha2026.9
uv pip sync --python .venv-ha2026.9/bin/python --require-hashes requirements-ha2026.9.lock
npm ci --prefix devtools/pyright --ignore-scripts --no-audit --no-fund
.venv-ha2026.9/bin/python -B -m unittest discover -s tests -v
.venv-ha2026.9/bin/python -B -m pytest -p no:cacheprovider --disable-socket --allow-unix-socket -o asyncio_mode=auto tests_ha
```

가벼운 기존 테스트와 실제 Home Assistant pytest 테스트는 별도 프로세스에서
실행합니다. 새 pytest 파일은 2026.9 작업에서 명시적으로 타입 검사하며,
기존 2026.8 환경에 pytest 플러그인을 추가하지 않습니다.

## Home Assistant 2026.10 호환성 검증

기존 검증 환경과 별도로 안정판 Home Assistant 2026.10.0, Python 3.14.7,
`uv==0.12.5`, Node.js 24를 사용합니다. 새 의존성 입력과 해시 잠금 파일은
`requirements-ha2026.10.in`, `requirements-ha2026.10.lock`입니다.

테스트 플러그인 `pytest-homeassistant-custom-component==0.13.370`의 메타데이터는
`homeassistant==2026.10.0b4`를 요구하므로, 단일 코어 버전만 지정한
`requirements-ha2026.10-overrides.in`으로 안정판을 선택합니다. CI는 설치된
Python·코어·플러그인 버전을 확인하고 전체 의존성 메타데이터를 검사합니다.
플러그인의 이 베타 버전 요구와 설치된 안정판 사이의 불일치가 정확히 하나일
때만 허용하며, 누락된 의존성이나 다른 버전 불일치는 실패로 처리합니다.

의존성 입력을 변경할 때는 같은 도구와 재정의 파일로 잠금을 다시 생성하세요.

```sh
uv pip compile --python 3.14.7 --generate-hashes --override requirements-ha2026.10-overrides.in requirements-ha2026.10.in -o requirements-ha2026.10.lock
```

전용 환경에서 `devtools/ha2026_10_typing.py`를 먼저 실행하세요. HA의 런타임
재노출과 Probatio 별칭을 타입 검사기가 인식하도록, 설치된 소스에서 `.pyi`
파일 네 개를 생성합니다. 원본 런타임 `.py`와 배포 메타데이터는 해시로
보호하며, 코어·플러그인·Probatio 버전과 실제 객체의 동일성도 확인합니다.
다른 가상환경, 예상과 다른 import 구조나 기존 타입 파일은 실패로 처리합니다.
동일한 생성 결과가 이미 있으면 다시 쓰지 않습니다. 이어지는 가드 테스트는
임시 파일과 새 프로세스로 실패 조건을 검사합니다. 이 준비는 2026.10 환경에만
적용되며, 기존 검증 환경과 통합구성요소의 런타임 import는 그대로 유지합니다.

다음 명령은 외부 서비스나 장치에 연결하지 않고, 기존 오프라인 테스트와 실제
Home Assistant 테스트를 별도 프로세스로 실행합니다. CI는 같은 검사에 위의
버전 및 메타데이터 검사를 추가합니다.

```sh
python -m pip install uv==0.12.5
uv venv --python 3.14.7 .venv-ha2026.10
uv pip sync --python .venv-ha2026.10/bin/python --require-hashes requirements-ha2026.10.lock
npm ci --prefix devtools/pyright --ignore-scripts --no-audit --no-fund
.venv-ha2026.10/bin/python -B devtools/ha2026_10_typing.py
.venv-ha2026.10/bin/python -B -m unittest discover -s devtools -p test_ha2026_10_typing.py -v
.venv-ha2026.10/bin/python -B -m pytest --collect-only -p no:cacheprovider --disable-socket --allow-unix-socket -o asyncio_mode=auto tests_ha
devtools/pyright/node_modules/.bin/pyright --project pyrightconfig.json --pythonpath .venv-ha2026.10/bin/python
devtools/pyright/node_modules/.bin/pyright --project pyrightconfig.json --pythonpath .venv-ha2026.10/bin/python tests_ha/conftest.py tests_ha/test_setup_entities.py tests_ha/test_serial_dependency.py tests_ha/test_restore_state.py tests_ha/test_change_source.py tests_ha/test_thermostat_command_target.py
.venv-ha2026.10/bin/python -B -m unittest discover -s tests -v
.venv-ha2026.10/bin/python -B -m pytest -p no:cacheprovider --disable-socket --allow-unix-socket -o asyncio_mode=auto tests_ha
.venv-ha2026.10/bin/ruff check --no-cache custom_components tests tests_ha
devtools/pyright/node_modules/.bin/pyright --project pyrightconfig.json --pythonpath .venv-ha2026.10/bin/python devtools/ha2026_10_typing.py devtools/test_ha2026_10_typing.py
.venv-ha2026.10/bin/ruff check --no-cache devtools/ha2026_10_typing.py devtools/test_ha2026_10_typing.py
```

## Home Assistant 2026.11 호환성 검증

기존 검증 환경과 별도로 Home Assistant 2026.11.0, Python 3.14.7, `uv==0.12.5`,
Node.js 24를 사용합니다. 의존성 입력과 해시 잠금 파일은
`requirements-ha2026.11.in`, `requirements-ha2026.11.lock`입니다.

CI 작업은 코어 버전 확인, Pyright, `tests` 아래의 unittest, Ruff를 실행합니다.
`tests_ha`는 Core 2026.11용 테스트 플러그인이 나오면 이 작업에 추가합니다.
저장소에서 검증하는 `pytest-homeassistant-custom-component==0.13.370`은 Core 2026.10을 요구합니다. 이 잠금 구성에서는 2026.11의 실제 Core pytest를 실행하지 않습니다.

```sh
uv venv --python 3.14.7 .venv-ha2026.11
uv pip sync --python .venv-ha2026.11/bin/python --require-hashes requirements-ha2026.11.lock
npm ci --prefix devtools/pyright --ignore-scripts --no-audit --no-fund
devtools/pyright/node_modules/.bin/pyright --project pyrightconfig.json --pythonpath .venv-ha2026.11/bin/python
.venv-ha2026.11/bin/python -B -m unittest discover -s tests -v
.venv-ha2026.11/bin/ruff check --no-cache custom_components tests tests_ha
```

## 화면 검증

`devtools/ui_check/check.py`는 실제 Home Assistant 2026.10을 Home Assistant 자체
화면 그대로 실행하고, 실제 월패드 대신 127.0.0.1에서 동작하는 합성 월패드를
연결합니다. 합성 월패드는 저장소 테스트에 있는 프레임만 보냅니다. 브라우저로
로그인한 뒤 구성 흐름으로 통합구성요소를 추가하고(포트 오류 메시지 포함), 한국어
문구와 기기, 엔티티, 해석된 온도 값을 확인하며, 온도조절기 기기 페이지, 환기 모드 선택기와 읽기 전용 일괄소등 상태를
1440×900 및 390×844 화면에서 확인하고 스크린샷을 저장합니다. 선택기는 키보드로
열고 Escape로 닫아 초점 복귀를 확인하며, 모드는 선택하지 않습니다.

이 검증은 운영 중인 Home Assistant, 월패드, 장치에 연결하지 않으며 어떤 명령도
보내지 않습니다. Home Assistant가 합성 월패드에 무엇이든 보내면 검증은 실패합니다.
네트워크가 필요하지 않지만, 네트워크가 있으면 새로 설치한 Home Assistant와
마찬가지로 온보딩 과정에서 기본 통합구성요소인 met, radio_browser,
google_translate가 시작되고 브라우저가 Home Assistant의 브랜드 이미지를 불러오므로
인터넷에 접속할 수 있습니다.

`requirements-ui.lock`으로 `.venv-ui`를 만들고 Chromium을 설치한 뒤, 실행할 때마다
`mktemp -d`로 새 디렉터리를 만들어 지정하세요. 이미 파일이 있는 디렉터리는 거부되므로
고정된 경로는 두 번째 실행에서 실패합니다. 스크린샷, Home Assistant 로그, 합성
월패드가 받은 내용이 그 디렉터리 아래에 저장됩니다.

```sh
python -m pip install uv==0.12.5
uv venv --python 3.14.7 .venv-ui
uv pip sync --python .venv-ui/bin/python --require-hashes requirements-ui.lock
.venv-ui/bin/python -m playwright install --with-deps chromium
out=$(mktemp -d /tmp/kocom-ui-check.XXXXXX)
.venv-ui/bin/python -B devtools/ui_check/check.py --out "$out/shots"
```

CI는 `.github/workflows/ui-check.yml`에서 같은 검증을 실행하고, 스크린샷과 로그를
7일 동안 보관합니다.
