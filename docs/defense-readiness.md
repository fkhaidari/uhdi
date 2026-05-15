# uhdi defense readiness

Single-file synthesis after the 0.9.2 audit (`docs/uhdi-spec.md` Appendix A,
2026-05-15). Defense is in ~3 weeks. Intent: rehearse the Q&A bank, work the
risk register down, smoke-test the three deck-referenced demo paths, file the
documentation gaps separately.

Sources: `docs/uhdi-spec.md` (§14 open questions, §15 projections, Appendix B.9-B.11),
`docs/uhdi-action-plan.md` (§3.4.1, §4.3, §5, §6, §7), `bench/README.md`
(Pending fixtures), and the deck index at
`/home/farid/thesis/text/visual/decks/defense/deck.js` (slide `solution/counter-demo.html`).

---

## 1. Reviewer Q&A bank (Russian)

### 1.1 «Почему не расширили HGLDD, а сделали новый формат?»

HGLDD навязывает фиксированную пару `hgl_loc`/`hdl_loc` (Appendix B.7,
`docs/uhdi-spec.md:3064-3066`) и `objects`-плоское представление, которое
несовместимо с pool-based dedup'ом (Appendix B.1, `docs/uhdi-spec.md:3026-3033`).
Если бы мы навесили `body[]`/`bp`/`dataflow` поверх HGLDD-документа, получился
бы гибрид: HGLDD-консьюмер пришлось бы трактовать как привилегированный, а
независимость формата от консьюмеров (которой и держится §1.2 «суперсет трёх
legacy форматов») разваливается. Полный аргумент в Appendix B.9
(`docs/uhdi-spec.md:3072-3078`) и в `docs/uhdi-action-plan.md:30-38`.
Демонстрационно это видно по §15: HGLDD получается одной из трёх проекций
наравне с hgdb и PDG, ни одна не имеет особого статуса (`docs/uhdi-spec.md:2730-3002`).

### 1.2 «Почему не dataflow/provenance в текущей версии?»

§10 dataflow и §12 provenance специфицированы в спеке полностью, но не
реализованы в эмиттере. Это сознательный scope cut, зафиксированный в
`docs/uhdi-action-plan.md:45` («никакого Phase 3+ задела»). Provenance
особенно — §12.13 (`docs/uhdi-spec.md:2613-...`) и §14
(`docs/uhdi-spec.md:2713-2722`) явно помечают её как research-grade:
для полноценного emitter'а нужна систематическая инструментация passes,
которая ещё не существует в CIRCT/Chisel; §12.5 MVP (4 passes) опускает
порог входа, но не закрывает работу. Честный ответ: это future work,
spec задаёт фундамент, реализация — следующая итерация после Phase 2.

### 1.3 «Почему Python-конвертеры, а не CIRCT-native?»

Если бы проекции (uhdi → HGLDD/hgdb/PDG) жили внутри CIRCT как C++ MLIR
passes, граница между «uhdi как формат» и «uhdi как внутренний IR CIRCT»
размылась бы. Python-проекторы читают тот же JSON, который читает любой
внешний инструмент, — этим демонстрируется portability формата. Полный
аргумент в Appendix B.10 (`docs/uhdi-spec.md:3080-3086`). C++ MLIR-код всё
же есть — это producer-сторона: `EmitUHDI.cpp` + два passes
`firrtl-uhdi-init` / `hw-uhdi-verilog-snapshot` (см.
`docs/uhdi-action-plan.md:46-47`); этот split (emitter в C++, проекторы в
Python) — структурное отражение split'а IR/format.

### 1.4 «Почему не сравнение с DWARF?»

Domain mismatch: DWARF предполагает PC-driven execution, software-style
лексические scope'ы и регистры; в железе нет PC, есть несколько одновременно
живых clock-доменов, «переменные» — это `bindKind: "port"/"reg"/"wire"/...`
(§6.2), категорий, которых у DWARF нет. Кроме того, ни один из трёх реальных
консьюмеров (Tywaves, hgdb, ChiselTrace) DWARF не читает — пришлось бы
строить три новых интеграции с нуля. Полный аргумент в Appendix B.11
(`docs/uhdi-spec.md:3088-3094`). Если предложить hardware-DWARF расширение,
оно станет четвёртым silo'ом, что прямо противоречит §1.1 problem statement.

### 1.5 «Что такое `<complex>` в `enableRef` и что такое `&`-joined строка — это hack?»

Это переходная MVP-форма §9.3, явно помеченная как transitional в спеке
(`docs/uhdi-spec.md:1512-1514`) и в action plan §3.4.1
(`docs/uhdi-action-plan.md:318-330`). Текущий emitter сериализует
AND-reduced predicate как `&`-разделённую строку stable_id'ов с `!`
для отрицания и литералом `<complex>` для unresolvable leaves; schema
тип `ExprOrVarRef` принимает строку без pattern, поэтому форма
schema-legal. Long-term target — materialize AND-reduction как entry в
`expressions` pool и писать `enableRef` как single id, разрешаемый там
же. Это требует C++ MLIR-работы в `circt:fk-sc/uhdi-pool` (~½ дня
эмиттер + ½ дня converter sync + перегенерация fixtures) и сознательно
отложено: переписка не блокирует ни M1, ни M2, schema принимает оба
варианта одновременно. Linter (§13, `docs/uhdi-spec.md:2646+`) исключает
MVP-форму из every-`*Ref`-resolves правила.

### 1.6 «Что такое `representations` и зачем N-way map вместо HGL/HDL пары?»

`representations` (§3.2, `docs/uhdi-spec.md:281-289`) — это map декларации
всех IR-уровней, отслеживаемых документом, с произвольными string-ключами
и `kind: "source"|"ir"|"hdl"`. Любая `Location.file` — индекс в
`files[]` конкретного representation. Зачем N-way: у CIRCT pipeline'а 4-5
осмысленных IR-уровней (Chisel → High FIRRTL → Low FIRRTL → HW dialect →
SystemVerilog), и для debug-info компилятора любой из них может быть
интересен. HGLDD-style dual pair (`hgl_loc`/`hdl_loc`) — это special
case N-way map'а (один `source`, один `hdl`); semantic mapping роль
делает через §3.3 `roles.authoring`/`simulation`/`canonical`. Полное
обоснование в Appendix B.7 (`docs/uhdi-spec.md:3064-3066`).

### 1.7 «В чём преимущество uhdi → hgdb projection перед SystemVerilog DPI / cosimulation?»

DPI — это runtime FFI между SV-симулятором и C++-кодом, она не предоставляет
**symbol table** (имя сигнала в исходнике → имя сигнала в Verilog), которая
нужна, чтобы отлаживать **по строкам исходного Chisel**. hgdb работает поверх
SQLite-таблиц `Instance`/`Variable`/`Breakpoint`/`Generator Variable` (§15.4.1,
`docs/uhdi-spec.md:2801-2811`); uhdi-эмиттер заполняет эти таблицы из
pool-based JSON. DPI и hgdb решают разные задачи: DPI — про вызовы из/в
симулятор, hgdb — про human debugging UI поверх SV-симуляции с маппингом
обратно в исходные термины. uhdi-format добавляет два преимущества над
тем, чтобы пользоваться hgdb напрямую: (а) единый источник истины для
нескольких консьюмеров (тот же JSON порождает HGLDD для Tywaves), (б)
expression'ы как AST, а не строки, поэтому их можно нормализовать /
анализировать программно (Appendix B.8, `docs/uhdi-spec.md:3068-3070`).

### 1.8 «Почему pool-based, а не nested everywhere?»

Pure nested (всё inline, hgdb-max-стиль) не делает dedup'а: один общий
`Bundle`-тип, на который ссылаются 50 портов, дублируется 50 раз — это не
scale'ится на RocketChip-class дизайны. Pure flat + integer indices
(PDG-max-стиль) заставляет каждый консьюмер заново строить hierarchy
обходом — overkill для 80% use cases (interactive debug, waveform).
Pool-based JSON с named refs — компромисс: O(1) dict lookup, dedup
работает, hierarchy не приходится восстанавливать. Бенчмарк размера
ожидаем 20-40% сжатие на SingleCycleCPU vs naive-inline baseline
(`docs/uhdi-action-plan.md:230`); это численный результат для главы 5
evaluation. Полный аргумент Appendix B.1 (`docs/uhdi-spec.md:3026-3033`).

### 1.9 «Кто реально читает JSON Schema?»

Три consumer'а на данный момент: (а) `uhdi_common.validate.UhdiValidator`,
который проверяет каждый input в Python-проекторах перед маппингом — это
работает в продакшен-пути `uhdi-to-hgldd` / `uhdi-to-hgdb` /
`uhdi-to-pdg`; (б) bench (`bench/test/test_pipeline.py`) запускает schema
validation как часть `(fixture × target)` ячейки до структурного diff'а;
(в) `docs/uhdi-spec.md` цитирует подмножества schema-snippet'ов в каждом
§N.4 чтобы spec и schema-файлы не расходились (audit 0.9.1 / 0.9.2 как
раз закрывал расхождения, см. changelog `docs/uhdi-spec.md:3017-3018`).
Schema — не для downstream HGLDD/hgdb consumer'ов: их формат входа
после проекции uhdi-агностичен. Schema нужна автору emitter'а и автору
любого нового projector'а, чтобы тестировать без полной CIRCT-сборки.

### 1.10 «Можно ли восстановить uhdi из существующего HGLDD/hgdb-документа?»

Reverse projection (HGLDD/hgdb/PDG → uhdi) — отдельная задача, не
specified в §15: ingestion требует auxiliary inputs (VCD, FIRRTL dump)
для type-width recovery в двух из трёх случаев (§15.1,
`docs/uhdi-spec.md:2738`). §15.6 формализует round-trip контракт:
`X → uhdi → X` — invariant для регрессионных тестов; `uhdi → X →
uhdi` — нет, потому что проекция lossy (legacy формат лишён полей,
которые uhdi хранит). Ingestion — future work, основная задача
которого — type recovery, а не format mapping.

---

## 2. Risk register

| # | Risk | Severity | Mitigation | Owner / Status |
|---|------|----------|------------|----------------|
| R1 | **Deck slide `solution/counter-demo.html` (deck.js:406-410) referenced "Demo на Counter", но `demo/counter/` не существует** — `Counter` — это только bench-fixture (`bench/fixtures/Counter.scala`), не self-contained `./run.sh` демо. | **P0** | Решить: либо использовать `demo/gcd/` как «GCD-demo» и поправить deck, либо склонировать `demo/gcd/` структуру вокруг Counter (5-10 минут). | author / open |
| R2 | `uhdi_to_hgldd` не проецирует `io`-Bundle scope'ы в HGLDD `objects[]`; native firtool эмитит по записи на каждое поле Bundle, наш — ничего (`bench/README.md:137-140`). Влияет на `GCD-tywaves`, `Fifo-tywaves`, `TrafficLight-tywaves`. Если демо tywaves в защите идёт через GCD/Fifo/FSM — port-view в Tywaves будет беднее native baseline'а. | **P0** | Bench skip-list в `_PENDING_FIXTURES` (`bench/test/test_pipeline.py:48`) маскирует это в CI. Открыть демо в Tywaves вручную **до** генерации screenshot'ов и зафиксировать deltу в главе 5 как known limitation, либо отложить Bundle projection в Phase 2.5. | author / open |
| R3 | `uhdi_to_hgdb` эмитит **ноль строк** для multi-arm `when`/`elsewhen` chain'ов; Counter (single `when`) работает (`bench/README.md:141-143`). GCD содержит loop с elsewhen, FSM — `switch`. `demo/gcd/design.db` существует (~40 KB), но содержимое для multi-arm веток не проверено. | **P0** | Запустить `hgdb-db demo/gcd/design.db` + `breakpoint where /abs/path/GCD.scala` руками; если breakpoint rows отсутствуют — либо упростить демо до single-when сценария, либо открыто признать gap в главе 5. README hgdb-сессия в `demo/README.md:151-159` должна **смотреть на actual current output**, не на старый screenshot. | author / open |
| R4 | `uhdi_to_hgldd` enum projection (`source_lang_type_info` / `enum_def_ref`) частично реализована, но не stress-tested на реальных FSM'ах с `ChiselEnum` + `switch` (`bench/README.md:144-147`). Влияет на `demo/fsm/` (TrafficLight) — единственный демо с ChiselEnum. | **P1** | Проверить, что Tywaves рендерит `state` как `Red/RedYellow/Green/Yellow`, а не `2'b00/.../11` (`demo/README.md:30`). Если нет — упомянуть как known limitation. | author / open |
| R5 | §9.3 MVP `&`-joined predicate string + sentinel `<complex>` — transitional, schema-legal, но при глубоком probing reviewer'ом может выглядеть как hack. Не блокирует M1/M2. | P1 | Подготовлен ответ Q1.5; action plan §3.4.1 (`docs/uhdi-action-plan.md:318-330`) формализует post-defense rewrite. | author / answered |
| R6 | Sanity-check'и **A13-A16** числятся «pre-sanity» в action plan §1.1-1.4, но статус-таблица §0 (`docs/uhdi-action-plan.md:13-19`) показывает ✅ только для A1-A4. A15 (Tywaves на HGLDD baseline) и A16 (Chisel `withDebug` → `circt_debug_*` intrinsics) — если падают, Phase 1 demo тихо деградирует. | P1 | Перепрогнать A13-A16 на текущем checkout'е (A13 trivial, A14 уже отражён фактом существующего emitter'а, A15/A16 требуют tywaves + scala-cli локально). Обновить таблицу §0. | author / unverified |
| R7 | Assumption'ы A5-A8 в §5 (`docs/uhdi-action-plan.md:432-441`) числятся «проверяется» / «решается»: A5 stable IDs deterministic, A6 capture-when conflict-free, A7 uhdi-attrs survive pipeline (≡A13), A8 SourceInfo сохраняется. Без явного зелёного флага — все они тянут P2 риск. | P2 | A5 → bench `manifest.toml` показывает детерминизм по hash; A6 → emitter работает на 5 демо без сбоев — соберём аргумент в evaluation; A7 ≡ A13; A8 → SourceInfo используется в Tywaves screenshot'е. | author / inferentially-passed |
| R8 | Bench `_PENDING_FIXTURES` skip-list (`bench/test/test_pipeline.py:48`) + `manifest.toml` semantics: «manifest stale: gap closed» fail (`bench/manifest.toml:20`). Когда projector fix приземлится и skip снимется, каждое `expected.*` для этой фикстуры, которое **больше не нужно**, провалит CI. Не риск для защиты, но риск для post-defense commit'ов. | P2 | Документируется в README; при удалении из skip-list прогонять `pytest bench/test -k <Name>` и чистить manifest. | author / future |
| R9 | §3.4.1 post-defense workstream (long-term `enableRef` shape) — fork в `circt:fk-sc/uhdi-pool` остаётся open. Не блокирует защиту, но reviewer может спросить «а это вообще закроется?». | P2 | Q1.5 объясняет, action plan §3.4.1 явно фиксирует scope: ~1 день C++ + перегенерация fixtures. | author / scheduled |
| R10 | `tools/install.sh` зависит от GitHub Releases на `fkhaidari/uhdi`; corporate сеть Yadro иногда роняет HTTPS на github.com без VPN (см. global CLAUDE.md). Если защитный demo'крутится не в Yadro-сети — низкий риск; в Yadro — нужно VPN. | P2 | Pre-defense pull-через-VPN и установка `~/.local/uhdi-tools/` на defense laptop. Альтернатива: docker image `ghcr.io/fkhaidari/uhdi-tools:b683085ef03e5ba2` (`tools/docker/image-tag.txt`). | author / mechanical |
| R11 | ChiselTrace демо-путь: deck слайды `overview/chiseltrace*.html` есть (deck.js:146-155), но **нет self-contained demo'а** в `demo/`. `uhdi_to_pdg/` — uncommitted (git status), fixtures под `converter/test/fixtures/expected/pdg/`. End-to-end для PDG идёт через bench-фикстуры, не через `./run.sh`. | P1 | Если на ChiselTrace-слайде нужен «вот мы запустили» — собрать минимальный путь через bench-fixture Counter + ChiselTrace GUI, либо признать что слайд показывает только проекцию (uhdi → PDG JSON), без runtime visualization. | author / open |
| R12 | `scripts/demo.sh` (упомянут в MEMORY.md как entry point) **не существует** в корне репо. Реальный entry — `demo/<name>/run.sh` → `demo/run.nu`. | P2 | Поправить MEMORY.md или добавить `scripts/demo.sh` как симлинк/обёртку. Не блокирует защиту. | author / cosmetic |

**Итого:** P0 = 3 (R1, R2, R3); P1 = 4 (R4, R5, R6, R11); P2 = 5.

---

## 3. Demo readiness

`demo/<name>/run.sh` во всех пяти случаях — символическая ссылка на
shared `demo/run.sh` (bash shim, ищет `nu`, дёргает `demo/run.nu` с
positional subcommand). Flags `--with-experimental-debug-intrinsics`
используются повсюду в Chisel source (`demo/*/app/src/*.scala`,
проверено `grep -rn`), старый `--with-debug-intrinsics` нигде не
встречается.

| Demo | `run.sh` exists | Flag current | Что показывает | Связан ли со слайдом | Smoke status |
|------|----------------|--------------|-----------------|-----------------------|--------------|
| `demo/gcd/` | ✅ symlink → `demo/run.sh` | ✅ `--with-experimental-debug-intrinsics` | UInt arithmetic, single module, простейший end-to-end. README hgdb console session (`demo/README.md:151-205`) использует именно GCD. | **Likely hgdb-slide proxy** (deck слайд называется «Demo на Counter», но фактический demo-репо имеет GCD как самый отполированный путь). Tywaves slide-set'у тоже подходит. | **MUST WORK.** `design.db` существует (40 KB, May 8); содержимое для multi-arm `when` веток **не проверено** — см. R3. Запустить `./run.sh build` + `hgdb-db design.db` руками. |
| `demo/fsm/` | ✅ symlink | ✅ verified | `ChiselEnum`-FSM (TrafficLight); Tywaves должен показать state как `Red/RedYellow/Green/Yellow` (`demo/README.md:30`). | Tywaves enum-rendering — потенциально tywaves slide. | Stress-test enum projection **не проведён** — R4. Запустить `./run.sh simulate` + tywaves вручную. |
| `demo/fifo/` | ✅ symlink | ✅ verified | `Decoupled<UInt>` + `SyncReadMem`; Bundle ports collapse в Tywaves struct view. | Tywaves bundle-rendering — потенциально tywaves slide. | Bundle projection в HGLDD broken (R2) — `io_*` поля могут не попасть в `objects[]`. |
| `demo/pipeline/` | ✅ symlink | ✅ verified | 3-stage MAC + два sub-Module'я; hierarchy navigation в tywaves; hgdb step across pipeline registers. | Возможный hgdb-slide или tywaves-hierarchy slide. | Single-when only внутри стейджей — hgdb path должен работать без multi-arm gap'а. Smoke вручную. |
| `demo/bus/` | ✅ symlink | ✅ verified | `Decoupled` вложенных `Bundle` (`Request{addr,data,write}` → `Response{data,ok}`). | Nested-record stress; не входит в deck'е явно. | Bundle projection broken (R2) — два уровня вложенности усугубляют gap. |

**Деки и привязки (deck.js → demo):**

- `overview/tywaves-usage.html` (slides 80-95) — overview-слайды без
  жёсткой привязки к конкретному `demo/<name>/`; скриншоты могут быть
  заранее сделанными.
- `overview/hgdb-usage.html` (slides 116-143) — overview hgdb на
  GCD-сессии из README, тоже не runtime-dependent.
- `overview/chiseltrace-usage.html` (146-155) — без demo-репо
  (см. R11).
- `solution/counter-demo.html` (406-410) — **финал демонстрация**,
  слайд именно про live-запуск. **Это место, где R1 кусается.**

**Pre-defense MUST-WORK путь (минимальный):**

1. `cd demo/gcd && ./run.sh build` — генерирует `design.uhdi.json`,
   `design.dd`, `design.db`. Должно завершиться без ошибок.
2. `./run.sh simulate` — генерирует `design.vcd`. Verilator должен
   быть в PATH.
3. `tywaves design.vcd --hgldd-dir . --top-module GCD --extra-scopes
   TOP svsimTestbench dut` — GUI открывается, иерархия видна, типы
   распознаются.
4. `./run.sh debug-server` + `./run.sh debug` в двух терминалах —
   hgdb console сессия с breakpoint'ами по строкам `GCD.scala`.

Если все 4 шага зелёные на GCD, hgdb+tywaves слайды защищаются. ChiselTrace —
отдельная история (R11).

---

## 4. Documentation gaps surfaced this pass

Беглый осмотр первых ~50 строк ключевых markdown'ов. Запись = реальная
несостыковка, не гипотетическая.

- **`README.md:73`** (`uv pip install ... --no-config`) — рабочий путь, но
  не упоминает что `--index-url` + `--no-config` нужны **только** в
  Yadro corp-сети. Для внешнего читателя — лишний noise; стоит
  пояснить.
- **`docs/uhdi-action-plan.md:13-19`** — таблица показывает ✅ только
  для A1-A4. A13-A16 описаны в §1.1-1.4 как «проверяется», но в
  итоговой таблице §5 (`docs/uhdi-action-plan.md:432-445`) их статус
  — `Pre-sanity (§1.x)`. **Пройти A13-A16 и проставить ✅ или
  unresolved** до защиты — иначе reviewer ткнёт в зазор. См. R6.
- **`docs/uhdi-action-plan.md:308-316`** (§3.4 Emitter extension) и
  **§3.4.1** (`docs/uhdi-action-plan.md:318-330`) — добавлен текущим
  audit'ом. Внутренний текст §3.4 описывает MVP, §3.4.1 описывает
  long-term shape. Они в одном секции, но §3.4 не указывает «текущая
  реализация — MVP, см. §3.4.1». Стоит добавить one-liner cross-ref.
- **`docs/uhdi-action-plan.md:511-515`** (§7.3 ожидаемые вопросы) —
  четыре вопроса перечислены, но ответов **в action plan нет**. Этот
  файл (`docs/defense-readiness.md` §1) их и закрывает. Стоит из
  §7.3 указать cross-ref сюда.
- **`bench/README.md:130-152`** «Pending fixtures» — секция актуальна
  (датирована 2026-05-15). Но 3 P0/P1 risk'а отсюда (R2/R3/R4) не
  попали в action plan §5 «Сводка предположений». Стоит добавить их
  туда или указать что risk register'а живёт в `defense-readiness.md`.
- **`tools/README.md:3`** ссылается на `ghcr.io/fkhaidari/uhdi-tools:<tag>`
  без явного указания `b683085ef03e5ba2` (текущий tag из
  `tools/docker/image-tag.txt`). Это by design (`<tag>` placeholder),
  но `bench/README.md:69` использует tag через `$(cat
  ../tools/docker/image-tag.txt)`. Сверить руками что image на
  ghcr.io действительно тегирован `b683085ef03e5ba2`.
- **`demo/README.md:151-205`** — README hgdb-сессия для GCD показывает
  работающие breakpoint'ы. **Это скриншот текущего поведения или
  старого?** Если GCD multi-arm when-gap (R3) обрезает breakpoint'ы,
  README расходится с current state. Проверить руками и либо
  переснять, либо упростить демо до single-when пути.
- **`MEMORY.md`** (privacy: global) упоминает `scripts/demo.sh` —
  путь не существует, см. R12.

---

## 5. Pre-defense punch list

В порядке зависимостей и дат (T-0 = defense day, ~21 день от
2026-05-15).

1. **T-21 → T-18: Закрыть P0 risk'и R1/R2/R3 (демо-смысловая
   готовность).**
   1. Поправить deck'овую ссылку `solution/counter-demo.html`: либо
      указывать на GCD как «Demo на GCD», либо собрать минимальный
      counter-demo как клон gcd-структуры. Решение записать в
      `docs/uhdi-action-plan.md` §3.6 / §7.
   2. Запустить руками `demo/gcd/run.sh build` + `hgdb-db design.db`
      → подтвердить что breakpoint rows присутствуют для всех
      `:=`-line'ов GCD.scala. Если multi-arm gap всё ломает — либо
      пропатчить uhdi_to_hgdb (приоритет), либо упростить GCD до
      single-when.
   3. Открыть `design.dd` в Tywaves для GCD/Fifo/FSM → решить, какой
      из трёх остаётся в defense-demo set'е. Bundle gap R2 может
      сузить выбор до Fifo или FSM, у которых меньше зависимость
      от Bundle objects[].

2. **T-18 → T-14: A13-A16 sanity-check'и (R6).**
   1. A13: `uhdi.test_attr` на `dbg.variable` → grep по post-firtool
      output (`docs/uhdi-action-plan.md:56-66`).
   2. A14: подтверждение что `dbg.scope_body`/`dbg.block` уже
      существуют в нашей fk-sc/uhdi-pool ветке (де-факто да, раз
      emitter работает).
   3. A15: Tywaves на native HGLDD из GCD — открывается ли вообще.
   4. A16: `withDebug` присутствует в Chisel sim'ах
      (`demo/*/app/src/*Sim.scala`) — `--with-experimental-debug-intrinsics`
      verified by grep.
   - Проставить ✅ в `docs/uhdi-action-plan.md:13-19`.

3. **T-14 → T-10: ChiselTrace путь (R11).**
   1. Запустить `uhdi-to-pdg bench/.cache/.../Counter*.uhdi.json` →
      получить `Counter.pdg.json`.
   2. Открыть в ChiselTrace GUI. Если работает — собрать static
      screenshot для `overview/chiseltrace-usage.html`. Если нет —
      переформулировать слайд как «projection works, GUI integration
      future work».

4. **T-10 → T-7: Документация финал.**
   1. README.md `--no-config` пояснение (gap §4 item 1).
   2. action plan §0 ✅-таблица обновлена (gap §4 item 2).
   3. action plan §3.4 cross-ref на §3.4.1 (gap §4 item 3).
   4. action plan §7.3 cross-ref на defense-readiness.md §1 (gap §4
      item 4).
   5. bench/README.md Pending fixtures cross-ref на risk register
      (gap §4 item 5).
   6. tools/README.md sanity check image tag (gap §4 item 6).
   7. demo/README.md hgdb-сессия пересмотрена против current
      output (gap §4 item 7).

5. **T-7 → T-3: Слайды + speaker notes.**
   1. Speaker note по §4.3 action plan'а
      (`docs/uhdi-action-plan.md:416-424`) — финализировать.
   2. 4 «очевидных» вопроса (§7.3) → ответы из §1 этого документа.
   3. Bonus questions (Q1.5-Q1.10) — мысленно прокатать вслух.
   4. Risk register — каждый P0 закрыт или явно объяснён в
      speaker note как «known limitation, mitigation X».

6. **T-3 → T-1: Репетиция вслух минимум 2 раза.** Action plan
   §7.3 last bullet требует. Записать на телефон, переслушать.

7. **T-1: Defense laptop checklist.**
   1. `~/.local/uhdi-tools/` установлен (`tools/install.sh all`),
      VPN до Yadro, если в corp-сети (R10).
   2. `cd demo/gcd && ./run.sh build && ./run.sh simulate` —
      зелёный.
   3. Tywaves + hgdb-replay + hgdb-debugger всё в PATH.
   4. Video-fallback'и (30-60s tywaves + 30-60s hgdb) записаны на
      случай сетевого / GUI-сбоя на проекторе.

8. **T-0: Defense.** Run-of-show: открыть demo/gcd/.bin/, дёрнуть
   `./run.sh simulate` за 30 секунд до слайда, переключиться на
   tywaves window. Если что-то падает — переключиться на video.

---

*— конец документа —*
