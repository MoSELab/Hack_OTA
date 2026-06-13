# HackOTA 3-Device Lab

다음 세 파일을 각각 한 장비에 복사하여 실행하는 의도적으로 취약한 OTA
교육 환경입니다.

| 장비 | 복사할 파일 | 역할 |
| --- | --- | --- |
| Laptop | `web_app.py` | Web UI, Update Server, MQTT Publish, 결과 Dashboard |
| Raspberry Pi 1 | `central_gateway.py` | MQTT 수신, CAN 전달, 결과 중계 |
| Raspberry Pi 2 | `ecu.py` | 단일 Cluster ECU, `cluster.py` A/B Update |

각 Python 파일은 HackOTA의 다른 Python 모듈을 필요로 하지 않는 독립형
파일입니다.

## 통신 구조

```text
Browser
   |
Laptop: web_app.py
   |
   | Anonymous MQTT 210.123.37.150:1883
   v
Raspberry Pi 1: central_gateway.py
   |
   | Physical CAN bus
   v
Raspberry Pi 2: Cluster ECU (`ecu.py`)
   |
   | CAN result frame
   v
Raspberry Pi 1 -> MQTT -> Laptop Dashboard
```

## 설치

### Laptop

```powershell
python -m pip install Flask paho-mqtt
```

기본 MQTT Broker는 인증과 TLS를 사용하지 않는
`210.123.37.150:1883`입니다.

### Raspberry Pi 1

```bash
python3 -m pip install paho-mqtt python-can
```

### Raspberry Pi 2

```bash
python3 -m pip install python-can
```

## CAN 준비

Gateway Raspberry Pi와 ECU Raspberry Pi를 CAN Transceiver로 연결합니다.
두 코드는 시작할 때 다음 명령과 같은 동작을 자동 수행합니다.

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up
```

기본 CAN Bitrate는 1 Mbps입니다. `sudo` 비밀번호 입력이 필요할 수 있습니다.
CAN 배선에는 CAN-H, CAN-L, 공통 GND와 종단저항을 사용합니다.

```text
OTA START:  0x700
OTA DATA:   0x701
OTA END:    0x702
OTA RESULT: 0x703
```

ECU Raspberry Pi에는 IP 연결이나 MQTT 설정이 필요하지 않습니다.

## 실행 순서

### 1. Laptop

```powershell
python web_app.py
```

입력 예:

```text
MQTT Broker IP [210.123.37.150]:
MQTT Broker Port [1883]:
Web Host [0.0.0.0]:
Web Port [5000]:
```

Broker IP와 Port는 Enter를 눌러 기본값을 사용할 수 있습니다.

### 2. Raspberry Pi 2 ECU

```bash
python3 ecu.py
```

입력 예:

```text
CAN Interface [socketcan]:
CAN Channel [can0]:
CAN Bitrate [1000000]:
OTA Base CAN ID [0x700]:
```

ECU 종류 입력은 없으며 항상 단일 `cluster_ecu`로 실행됩니다.

### 3. Raspberry Pi 1 Central Gateway

```bash
python3 central_gateway.py
```

입력 예:

```text
MQTT Broker IP [210.123.37.150]:
MQTT Broker Port [1883]:
CAN Interface [socketcan]:
CAN Channel [can0]:
CAN Bitrate [1000000]:
OTA Base CAN ID [0x700]:
```

### 4. Web 접속

Laptop에서:

```text
http://127.0.0.1:5000
```

다른 교육용 PC에서 접속한다면:

```text
http://192.168.0.10:5000
```

## Cluster ECU A/B 구조

`ecu.py`를 처음 실행하면 다음 구조를 자동 생성합니다.

```text
cluster_ecu_data/
├─ active_slot.txt
├─ status.json
└─ slots/
   ├─ slot_a/
   │  └─ cluster.py
   └─ slot_b/
      └─ cluster.py
```

초기 Active Slot은 `A`이며 기본 Cluster 화면이 실행됩니다. Web에서
Python Cluster 프로그램을 업로드하면 Update Server가 즉시 MQTT로
Publish합니다. 화면 하단에서는 업로드, Publish, ECU 결과 로그를 확인할
수 있습니다. 업로드 파일명과 관계없이 ECU에서는 비활성 Slot의
`cluster.py`로 저장됩니다.

업데이트 순서:

1. CAN으로 Cluster 프로그램을 수신합니다.
2. 비활성 Slot의 `cluster.py`를 덮어씁니다.
3. 현재 Cluster를 종료하고 새 Slot의 `cluster.py`를 실행합니다.
4. 새 프로그램이 3초 동안 종료되지 않으면 Active Slot을 전환합니다.
5. 바로 종료되면 기존 Slot의 `cluster.py`를 다시 실행합니다.

Web 테스트에는 `sample_firmware/cluster_v2.py`를 업로드할 수 있습니다.
Version을 `2.0.0`으로 입력하고 배포하면 비활성 Slot에 설치됩니다.

## 생성되는 데이터

* Laptop: `hackota_uploads`
* ECU Raspberry Pi: `cluster_ecu_data`

이 디렉터리를 삭제하면 업로드 패키지와 ECU Slot 상태를 초기화할 수
있습니다.
