[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

# Kocom Wallpad Integration for Home Assistant
Home Assistant를 위한 Kocom Wallpad 통합구성요소

이 유지보수 포크는 [lunDreame의 원본 통합구성요소](https://github.com/lunDreame/kocom-wallpad)를 기반으로 합니다.
원저작권과 Apache 라이선스는 그대로 유지됩니다.

Home Assistant 2026.8.0 이상이 필요하며, 이는 테스트된 개발 기준과 일치합니다.
이전 버전의 Home Assistant는 이번 릴리스에서 더 이상 지원하지 않으므로,
설치 전에 Home Assistant를 먼저 업그레이드해 주세요.

## 기여
문제가 있나요? [Issues](https://github.com/ninthsword/kocom-wallpad/issues) 탭에 작성해 주세요.

- 더 좋은 아이디어가 있나요? [Pull requests](https://github.com/ninthsword/kocom-wallpad/pulls)로 공유해 주세요!
- 이 통합을 사용하면서 발생하는 문제에 대해서는 책임지지 않습니다.

도움이 되셨나요? [카카오페이](https://qr.kakaopay.com/FWDWOBBmR) [토스](https://toss.me/lundreamer)

## 설치
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ninthsword&repository=kocom-wallpad&category=Integration)

이 통합을 설치하려면 이 GitHub Repo를 HACS Custom Repositories에 추가하거나 위의 배지를 클릭하세요. 설치 후 HomeAssistant를 재부팅하세요.

1. **기기 및 서비스** 메뉴에서 **통합구성요소 추가하기**를 클릭합니다.
2. **브랜드 이름 검색** 탭에 `코콤 월패드`을 입력하고 검색 결과에서 클릭합니다.
3. 아래 설명에 따라 설정을 진행합니다:
   - 호스트: EW11 장치의 IP 주소
   - 포트: EW11 장치의 포트 (기본값: 8899)
4. 설정이 완료된 후, 컴포넌트가 로드되면 생성된 기기를 사용하실 수 있습니다.

### 준비
- 기본적인 환경에선 EW11 장치 하나 필요 추가적인 인터폰 제어 시에는 기존 장치 포함 하나 더 필요
- 인터폰 결선의 경우 [해당](https://blog.oriang.net/45) 링크 참조

## 기능

| 기기       | 지원  | 속성                           |
|-----------|------|-------------------------------|
| 조명 (디밍) | O    |                               |
| 일괄소등    | O    |                               |
| 콘센트      | O    |                               |
| 난방       | O    | 외출 모드                        |
| 에어컨     | O    |                                |
| 환기       | O    |                                |
| 가스       | O    | 잠금만 지원                       |
| 실내 공기질  | O    |                                |
| 모션(현관)  | O    |                                |
| 인터폰      | X    |                                 |
| 엘리베이터   | O    | 방향, 층수                       |

- **초기 장치 추가 시에는 최초 한번은 장치를 ON/OFF 하셔야 합니다.**
- 엘리베이터의 경우 현관 스위치가 있는 경우 현관 스위치에서 호출하셔야 정상적으로 등록됩니다.
- 기기 지원 및 기타 요청 사항은 [유지보수 이슈 트래커](https://github.com/ninthsword/kocom-wallpad/issues)를 이용해 주세요.

## 디버깅
- 문제 파악을 위해 아래 코드를 `configuration.yaml` 파일에 추가 후 HomeAssistant를 재시작해 주세요.
- 디버깅 외에는 활성화하지 마세요.

디버깅 관련 문제는 [유지보수 이슈 트래커](https://github.com/ninthsword/kocom-wallpad/issues)를 통해 제보해 주세요.

```yaml
logger:
  default: info
  logs:
    custom_components.kocom_wallpad: debug
```

## 라이선스
Kocom WallPad 통합은 [Apache License](./LICENSE)를 따릅니다.

## 개발 검증

Python 3.14.7, uv 0.12.5, Node.js 24를 사용합니다. 해시 잠금된 개발 의존성에는
Home Assistant 2026.8.0, pyserial-asyncio-fast 0.16, Ruff 0.16.4가 포함됩니다.
Pyright 1.1.413은 `devtools/pyright` 아래에 격리되어 있습니다. 런타임 시리얼 의존성은 테스트된 버전으로 고정되어 있습니다.
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

Pyright는 14개 통합구성요소 모듈과 오프라인 회귀 테스트 모듈 전체를
실제 개발 의존성을 기준으로 검사합니다. Config-flow 테스트는 프로토콜 shim과 분리된
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
