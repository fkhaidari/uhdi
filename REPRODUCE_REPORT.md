# Воспроизводимость uhdi bench — отчёт агента

**Дата:** 2026-05-24  
**База:** ubuntu:24.04 (свежий podman-контейнер, без предустановок)  
**Scope:** tywaves + pdg targets (hgdb_circt и hgdb_firrtl требуют ghcr image с предсобранным CIRCT)

---

## Шаги и результаты

### 1. Базовые пакеты
**PASS**  
`apt-get install curl ca-certificates git python3 python3-venv python3-pip tar xz-utils` — установились без проблем.

### 2. install.sh all --prefix /work/install
**PARTIAL**  
Команда: `cd /work/uhdi && bash tools/install.sh all --prefix /work/install`

| Компонент | Статус | Примечание |
|-----------|--------|------------|
| nu (nushell 0.112.2) | PASS | bootstrap скачал автоматически |
| firtool | PASS | `firtool-linux-x86_64-firtool-v0.1.3.tar.gz` |
| hgdb-py | PASS | `hgdb-py-linux-x86_64-firtool-v0.1.3.tar.gz` |
| chisel | PASS | печатает JitPack snippet (v0.1.4-uhdi), файлов не кладёт |
| tywaves | PASS | `tywaves-linux-x86_64-firtool-v0.1.3.tar.gz` |
| chiseltrace | **FAIL** | нет asset `chiseltrace-linux-x86_64-*.tar.gz` на релизе `firtool-v0.1.3` |
| hgdb-cli | PASS | venv + pip install hgdb-debugger/libhgdb/uhdi-converter; все 6 converters |

**Итог bin/:** `firtool`, `hgdb`, `hgdb-db`, `hgdb-replay`, `nu`, `tywaves`, `uhdi-to-hgdb`, `uhdi-to-hgldd`, `uhdi-to-pdg`

**Gotcha:** первый запуск install.sh падал с ошибкой  
`Cannot update time stamp of directory 'src/uhdi_converter.egg-info'`  
когда `/work/uhdi` был смонтирован readonly. pip делает editable install (`-e`) прямо в исходном дереве, а это требует rw-доступа к репозиторию. Решение: монтировать `:rw` (или копировать converter в rw-директорию).

### 3. Python deps (converter + bench)
**PASS**  
```
python3 -m venv /work/bench-venv
/work/bench-venv/bin/pip install -e /work/uhdi/converter -e '/work/uhdi/bench[dev]'
```

### 4. test-install.nu smoke test
**SKIP** (не запускался отдельно — результаты install.sh подтверждены напрямую через ls)

### 5. pytest bench
**PASS (с ожидаемыми skip)**  
```
export FIRTOOL=/work/install/bin/firtool
export HGDB_PY=/work/install/lib/hgdb/bindings/python
cd /work/uhdi/bench && /work/bench-venv/bin/pytest -v --tb=short
```

Результат: **13 passed, 27 skipped in 0.09s**

| Тест | Результат |
|------|-----------|
| test_downgrade_fir.py (8 тестов) | PASS |
| test_runner_helpers.py (5 тестов) | PASS |
| test_pipeline.py — все ячейки (27) | SKIP |

**Причина skip всех pipeline-ячеек:** `scala-cli not on PATH; install from https://scala-cli.virtuslab.org/`  
scala-cli — единственная недостающая зависимость для реального прогона Scala→FIR→UHDI→diff.

---

## Проблемы и blockers

### Blocker 1: chiseltrace не выложен на релизе
```
Error: no asset matching 'chiseltrace-linux-x86_64-*.tar.gz' in fkhaidari/uhdi@firtool-v0.1.3
```
Релиз `firtool-v0.1.3` не содержит chiseltrace tarball. Нужно загрузить его через `release-chiseltrace.nu` (требует Rust 1.75+, npm, cargo-tauri) или добавить в release workflow.

### Blocker 2: install.sh требует rw-доступ к репозиторию
hgdb-cli делает `pip install -e <repo>/converter` — editable install создаёт `*.egg-info` прямо в дереве. При монтировании `:ro` падает с ошибкой timestamp. Для внешних пользователей, которые клонируют репо, это не проблема, но документировать стоит.

### Blocker 3: scala-cli не устанавливается install.sh
install.sh не устанавливает scala-cli. Без него все pipeline-ячейки bench пропускаются. Нужно либо добавить установку в install.sh, либо явно задокументировать как prerequisite.

Установить scala-cli:
```sh
curl -sSLf https://scala-cli.virtuslab.org/get | sh
# или через coursier:
cs install scala-cli
```

### Не-блокеры
- **hgdb_circt, hgdb_firrtl targets:** всегда skip без ghcr image (нет `HGDB_CIRCT_FIRTOOL` и `HGDB_FIRRTL_JAR`). Это ожидаемо — они требуют предсобранного CIRCT.
- **chisel publishLocal:** install.sh только печатает JitPack snippet. Для bench нужен `~/.ivy2/local` (или ghcr image где он baked). Без него scala-cli не найдёт chisel fork. Это вторичный blocker — сначала нужно установить scala-cli.

---

## Выводы

### Что работает из коробки (после `install.sh all`)
- firtool с `--emit-uhdi`
- hgdb-py bindings
- tywaves viewer
- все 6 UHDI converters (uhdi-to-hgldd, uhdi-to-hgdb, uhdi-to-pdg и др.)
- unit-тесты bench (downgrade_fir, runner_helpers) — 13/13 pass

### Что не работает
1. **chiseltrace:** нет tarball на текущем релизе
2. **Pipeline-ячейки bench:** нужен scala-cli + chisel publishLocal

### Что нужно для полного воспроизведения (все ячейки bench)

**Вариант A — install.sh + ручные шаги:**
```sh
git clone https://github.com/fkhaidari/uhdi
cd uhdi
tools/install.sh all --prefix ~/.local/uhdi-tools
# + scala-cli
curl -sSLf https://scala-cli.virtuslab.org/get | sh
# + chisel publishLocal (оба форка: rameloni-chisel + fkhaidari/chisel)
# + hgdb_circt/hgdb_firrtl targets требуют ghcr pull
```

**Вариант B — ghcr image (полный 4/4 targets):**
```sh
TAG=$(cat tools/docker/image-tag.txt)
docker run --rm -v "$PWD":/work -w /work \
    ghcr.io/fkhaidari/uhdi-tools:$TAG \
    bash -c 'pip install -e ./converter -e "./bench[dev]" && cd bench && pytest -v'
```
Все зависимости baked, scala-cli preinstalled, оба Chisel форка publishLocal'ены.
