# uhdi в CIRCT: руководство к действию

**Phase 1 (Tywaves) + Phase 2 (hgdb). План как последовательность шагов.**

---

## 0. Контекст и главный тезис

Таргет демонстрации — средний SoC (не RocketChip). Защита ограничена результатами Phase 1 + Phase 2.

Sanity-checks A1–A4 **пройдены**:

| # | Результат | Последствие |
|---|---|---|
| A1 | ✅ InlineAnnotation → `dbg.scope "l", "Leaf"` создаётся корректно | inline-tracking работает без пре-пасса |
| A2 | ✅ LowerTypes сохраняет nested `dbg.struct` при плоских SV-портах | Tywaves bundle-view получит корректные данные |
| A3 | ✅ Unused `observed` reg выжил после DCE | `dontTouch` не нужен |
| A4 | ✅ `dbg.variable` имеет attr-dict через assemblyFormat — discardable attrs вешаются без расширения dialect | основной путь, не fallback |

Все риски [C]-уровня из первой редакции плана сняты. Остаются A13–A16 (см. §5), проверяются в §1.

### Тезис для защиты

uhdi — унифицированный формат отладочной информации для hardware-генераторов, построенный как *суперсет* существующих форматов (hgdb, HGLDD, PDG). Работа демонстрирует:

1. **Формат** — N-way representations, pool-based structure, layered optionality (spec).
2. **Инфраструктура** — два CIRCT pass'а и один emitter, производящие uhdi из Chisel-дизайнов.
3. **Независимые проекции** — `uhdi-to-hgldd` и `uhdi-to-hgdb` как Python-конвертеры. Python умышленный: reference implementation, явно отделённая от компилятора, подчёркивает, что формат — независимая сущность, а не внутреннее представление CIRCT.

### Почему pool-based формат с нуля, а не расширение HGLDD

Альтернатива — взять HGLDD как базу и наращивать к нему ключи `body[]`/`bp` для hgdb-use-case. Короче, но:

- narrative "unified format как суперсет" размывается — формат остаётся гибридом.
- Phase 3+ заделы (dataflow, temporal, provenance) становятся пристроем к HGLDD-shape, а не естественным расширением pool-based структуры.
- thesis-defence слабее: "выполнили одну проекцию поверх чужого формата" против "формат — независимая сущность, две проекции это демонстрируют".

Pool-based path тяжелее, но даёт прочную основу. Сознательный выбор.

### Что это значит для кода

- Базовая ветка — `fk-sc/debug-info` (Chisel intrinsics + расширение `dbg` dialect). От неё отпочковываемся, uhdi-работа начинается с чистого листа.
- Минимум новых passes: **два** в Phase 1, **один-два** в Phase 2.
- Минимум нового в dialect: attributes (Phase 1) + statement-ops с регионами (Phase 2).
- **Никакого Phase 3+ задела**: representations фиксирована как пара `(chisel, verilog)`, status не эмитится, dataflow/temporal/provenance не трогаем.
- Emitter — один файл `EmitUHDI.cpp` с флагом `--emit-uhdi`.
- Converters (`uhdi-to-hgldd`, `uhdi-to-hgdb`) — Python-скрипты, decoupled от CIRCT build.

---

## 1. Pre-implementation: sanity-checks и baseline

A1–A4 уже пройдены. До начала реализации закрываем четыре дополнительные проверки.

### 1.1 Check A13 — discardable attrs переживают pipeline

Критично для обоих pass'ов Phase 1. MLIR обычно сохраняет discardable attrs, но отдельные CIRCT passes иногда стирают их вручную.

Тест:

- Проставить `uhdi.test_attr = "hello"` на `dbg.variable` на FIRRTL-уровне (вручную в MLIR-fixture).
- Прогнать full pipeline до HW-dialect: `firtool --ir-hw test.fir`.
- Grep по output: attr должен сохраниться на соответствующем `dbg.variable`.

**Plan B, если теряются:** зарегистрировать `uhdi.stable_id` и `uhdi.repr_entry` как **named attributes** в `DebugOps.td` — 5–10 строк tablegen, добавляющих поля в `dbg.variable` / `dbg.scope` / `dbg.struct` / `dbg.array`. MLIR гарантирует survival зарегистрированных attrs через все стандартные passes, потому что op description их явно декларирует. FusedLoc как fallback **не использовать**: location-merging passes переписывают агрессивнее, чем op-level attrs.

### 1.2 Check A14 — dbg dialect расширяемость region-ops

Для Phase 2 нужны `dbg.scope_body` и `dbg.block` с регионами. В текущем `dbg` dialect (ветка `fk-sc/uhdi`) — только leaf ops.

Шаги:

- Прочитать `include/circt/Dialect/Debug/DebugOps.td` в своей ветке.
- Определить, есть ли уже region-ops или нужно добавлять.

Варианты реализации:

- **Предпочтительно:** добавить region-ops в `dbg` dialect напрямую в своём форке. Контроль над диалектом у тебя, конфликтов с upstream нет (это отдельная ветка).
- **Fallback, если хочется меньше diff'а к dbg:** создать отдельный `uhdi_body` dialect с нужными region-ops. Больше кода, меньше взаимодействия с существующими `dbg` ops.

### 1.3 Check A15 — Tywaves на HGLDD baseline

Baseline для валидации M1. Нужно убедиться, что Tywaves вообще запускается и корректно парсит HGLDD на наших демо-дизайнах — это reference-точка, с которой будет сравниваться `uhdi-to-hgldd` output.

Шаги:

- Взять демо-дизайн (GCD), собрать через rameloni-chisel + rameloni-circt: `firtool -g --emit-hgldd --hgldd-source-prefix=...`.
- Открыть полученный HGLDD в Tywaves.
- Убедиться, что сигналы, bundles и иерархия показываются.

**Известная ловушка:** без явного `--hgldd-source-prefix` emitter выдаёт `"file_info": [".."]` с мусорным file-index — Tywaves не загрузит. Всегда передавать prefix.

**Plan B, если не открывается после prefix:** исследовать конкретную причину (Tywaves ждёт specific schema version / specific fields). Это blocker-proxy для M1 — Phase 1 стартовать нельзя, потому что валидация M1 через Tywaves GUI не пройдёт.

### 1.4 Check A16 — Chisel withDebug конец цепочки

Chisel fork `fk-sc/debug-info` эмитит `circt_debug_*` intrinsics только при включённом `withDebug`. Без этого флага emitter получит голый `MaterializeDebugInfo` output без source-language info → Tywaves покажет flat сигналы, Phase 1 визуально деградирует.

Тест:

- Взять демо-дизайн (GCD).
- Собрать его через ChiselStage с явно включённым withDebug.
- Убедиться, что промежуточный FIRRTL содержит `circt_debug_var` intrinsics.
- Прогнать через firtool и убедиться, что UHDI JSON содержит `source_lang_type_info`.

Если `withDebug` забыт — defect проявится на валидации M1 и заставит пересобирать весь demo-набор.

---

## 2. Phase 1: pool-based uhdi + Tywaves

### 2.1 Архитектурный принцип

Emitter — **passive reader**. Отладочная информация не трекается, только читается из существующих свойств CIRCT:

- `dbg.*` ops живут через весь pipeline (A13 — подтверждается в §1.1).
- Inlining создаёт explicit `dbg.scope` (A1 ✅).
- LowerTypes сохраняет `dbg.struct`/`dbg.array` (A2 ✅).
- DCE не удаляет значения с dbg-uses (A3 ✅).

Последовательность:

1. Проставить stable IDs на dbg-ops (init pass, §2.2).
2. Snapshot'нуть финальные Verilog-имена (snapshot pass, §2.3, параллельный ExportVerilog).
3. Сериализовать в pool-based JSON (emitter, §2.4).
4. Конвертировать в HGLDD (Python-скрипт, §2.5).
5. Валидировать end-to-end через Tywaves (§2.6).

### 2.2 Pass `firrtl-uhdi-init`

**Положение:** после `MaterializeDebugInfo` / `LowerIntrinsics`. dbg ops материализуются этими пассами — раньше нечего аннотировать.

**Действие:** walk всех `dbg.variable` / `dbg.scope` / `dbg.struct` / `dbg.array`. Для каждого:

- Вычислить `stable_id = <kind>_<hash_prefix>_<counter>`:
  - `hash_prefix` = blake2b(name + type + scope-path), 8 hex chars.
  - `counter` разрешает коллизии внутри одного hash-prefix.
  - Стабильно между прогонами (нужно для diff-валидации в §2.6).
- Проставить attribute `uhdi.stable_id`.
- Заполнить attribute `uhdi.repr_entry` для ключа `"chisel"`: name + source loc из SourceInfo.

**Тесты:** `.mlir` FileCheck с 2–3 input-IR fixtures (leaf module; module с inlined scope; module с lowered bundle).

**Объём:** ~50–100 LOC C++.

### 2.3 Pass `hw-uhdi-verilog-snapshot`

**Положение:** параллельно ExportVerilog, не после. Работает на том же HW-dialect IR, с которого ExportVerilog эмитит Verilog.

**Источник имён:** `NameLoc "emitted"` и FusedLoc `"verilogLocations"`, которые `PrettifyVerilogNames` проставляет на HW ops перед ExportVerilog. Образец интеграции — `EmitHGLDDPass` в CIRCT, см. `tools/firtool/firtool.cpp` (wiring для HGLDD).

**Действие:** для каждой dbg-op с `uhdi.stable_id` найти соответствующий HW-op по tracking chain, извлечь Verilog-name и source location из NameLoc/FusedLoc. Заполнить `uhdi.repr_entry` для ключа `"verilog"`.

**Если dbg-op не привязан напрямую к HW-op** (например, чистый `dbg.struct` над lowered scalars): рекурсивно пройти по operands в поисках валидного HW-ref.

**Тесты:** `.mlir` FileCheck по образцу EmitHGLDD тестов.

**Объём:** ~300–500 LOC C++.

### 2.4 Emitter `export-uhdi` (pool-based)

Новый файл `lib/Target/DebugInfo/EmitUHDI.cpp`, флаг `--emit-uhdi`. Wiring в `tools/firtool/firtool.cpp` по образцу существующего `EmitHGLDDPass`.

```cpp
struct UhdiEmitter {
  DenseMap<Type, std::string>       typePool;
  DenseMap<Operation*, std::string> exprPool;
  std::map<std::string, VarJson>    varPool;
  std::map<std::string, ScopeJson>  scopePool;

  LogicalResult run(ModuleOp top, raw_ostream &os);
};
```

**Этапы:**

1. Фиксированный `representations` manifest: две записи `chisel` + `verilog`.
2. Walk `dbg.scope` → `scopePool`:
   - без `scope` operand + `hw.module` → `"module"`
   - без `scope` operand + `hw.module.extern` → `"extmodule"`
   - со `scope` operand → `"inline"`
3. Walk `dbg.variable` / `dbg.struct` / `dbg.array` → `varPool` + `typePool`.
4. Dedup:
   - Типы по structural equality.
   - Expressions по `(opcode, operands)` tuple.
5. Сериализация через `llvm::json::OStream`.

**Правила упрощений:**

- `status` не эмитится вообще (implicit preserved per spec §6.3).
- Expressions inline если uses_count == 1, named если ≥2.
- Chunking **не** реализуем — средний SoC не требует.
- CBOR **не** реализуем — JSON достаточно.
- Bundle в consolidated form (одна variable с struct-type, per §6.7) — оптимально для Tywaves.

**Тесты:** integration — прогнать на 4 эталонных дизайна (см. §2.6), валидировать output по spec schema через `jsonschema`.

**Объём:** ~1000–1500 LOC C++. Основная работа Phase 1.

### 2.5 CLI tool `uhdi-to-hgldd` (Python)

Straightforward field mapping согласно spec §15.3.

Задачи:

- Загрузить uhdi JSON, валидировать по schema (`jsonschema` library).
- Пройтись по variables, сгенерировать HGLDD объекты.
- Dedup struct-типов (HGLDD ожидает deduplicated structs).
- Packed/unpacked range conversion для vectors.

**Объём:** ~600–900 LOC Python.

### 2.6 Валидация Phase 1 (M1)

Набор тестовых дизайнов:

| Дизайн | Контролирует |
|---|---|
| GCD | Базовая функциональность, регистры, простые when'ы |
| FIFO (~20 signals) | Memory, Vec, небольшой control flow |
| SingleCycleCPU (учебный RISC-V) | Hierarchy, bundles, параметризация |
| SoC с 2–3 модулями + InlineInstance | `kind: "inline"` в uhdi scope tree |

**Метрика:** Tywaves на **нашем** `uhdi-to-hgldd` output показывает то же hierarchy/typed/values tree, что на **native** HGLDD.

**Реализация:**

1. Визуальный diff в Tywaves GUI — 4 дизайна, быстрый sanity.
2. Python-скрипт canonical JSON diff: нормализация обоих HGLDD (sort keys, stable ID-аннотация, whitespace) и structural diff.
3. Дополнительная метрика для evaluation: pool-based compression — подсчёт `(inline expressions / total)`, struct dedup rate, размер файла vs naive-inline baseline. На SingleCycleCPU ожидаемо 20–40% сокращение. Даёт численный результат для главы 5.

**M1 closed:** все 4 дизайна показываются в Tywaves идентично native HGLDD (diff по canonicalized JSON пустой или расхождения объяснены).

---

## 3. Phase 2: uhdi + hgdb

### 3.1 Что добавляется

- Capture-when pass для control flow и AND-reduced enable conditions.
- scope body — statement tree внутри scope, переживает ExpandWhens.
- Breakpoint metadata (только `enableRef`).
- Python CLI tool `uhdi-to-hgdb`.

### 3.2 Новые элементы dialect

**Ops** (внутри dbg scope body region):

- `dbg.scope_body` — region-containing op, одна на `dbg.scope`.
- `dbg.block` — region-op с `guardRef` attribute для when-nesting.
- `dbg.connect_stmt`, `dbg.decl_stmt` — statement-ops.
- `dbg.assert_stmt`, `dbg.assume_stmt`, `dbg.cover_stmt` — если в дизайне есть verification.
- `dbg.expression` — AST-узел с opcode и operands.

**Attribute:**

- `#dbg.bp` — только поле `enableRef`. Остальные (watchpoint, throttle, category, message) не эмитим.

Расширение делается в своей ветке. Форма — по результату §1.2: либо в `dbg` напрямую, либо в отдельном `uhdi_body` dialect.

### 3.3 Pass `firrtl-uhdi-capture-when`

**Положение:** между Inliner и ExpandWhens. Inliner проходит до нас (корректно обрабатывает `dbg.scope`), ExpandWhens после (разрушает `firrtl.when`, но наш `dbg.scope_body` автономен).

**Алгоритм (псевдокод):**

```
walkRegion(region, condStack, intoRegion):
  для каждой op в region:
    match op:
      firrtl.when:
        guard = buildDbgExpr(op.condition)
        thenBlock = create dbg.block{guardRef=guard} in intoRegion
        walkRegion(op.thenRegion, condStack ++ [guard], thenBlock.body)
        if op.hasElse:
          notGuard = buildDbgExpr(not(op.condition))
          elseBlock = create dbg.block{guardRef=notGuard} in intoRegion
          walkRegion(op.elseRegion, condStack ++ [notGuard], elseBlock.body)
      firrtl.connect:
        enable = andReduce(condStack)
        create dbg.connect_stmt{
          varRef:   stableIdOf(op.dest),
          valueRef: buildDbgExpr(op.src),
          bp:       #dbg.bp{enableRef = stableIdOf(enable)}
        } in intoRegion
      ... (assert/assume/cover, declarations)
```

**Три критические тонкости:**

1. **`dbg.expression` ссылается на `dbg.variable`, не на raw FIRRTL SSA.** ExpandWhens потом будет всё менять в FIRRTL. Если condition — `firrtl.and %a, %b`, сначала ищем `dbg.variable` на `%a` и `%b`; если нет — создаём синтетический через stable_id.

2. **Memoization AND-reduction.** Content-addressable cache по отсортированному вектору operand-stable_ids. В минимуме можно начать **без** memoization. На среднем SoC приемлемо (~10K expr ops). Добавлять при реальной необходимости.

3. **Автономность от ExpandWhens.** `dbg.scope_body` не должен иметь SSA-зависимостей от `firrtl.when`. ExpandWhens разрушает when-structure — наш region переживает.

**Тесты:** `.mlir` FileCheck — flat when, when/else, nested when-в-when, when с connect к агрегату.

**Объём:** ~500–900 LOC C++.

**Plan B, если pass буксует:**

- Уровень A: не поддерживать elsewhen chains — только when/else. Покрывает 90% паттернов.
- Уровень B: не делать memoization. Принять раздутый IR.
- Уровень C: наивный AND-reduce с inline AST в `bp` attribute, без exprPool. Эмитит повторы, работает.
- Уровень D: вход в Emergency §6.

### 3.4 Emitter extension

Добавляется в `EmitUHDI.cpp`:

- Walk `dbg.scope_body` region → uhdi `body` array. **Критично: pre-order, порядок значим** (FIRRTL last-connect semantics).
- Сериализация `#dbg.bp` → uhdi `bp` field (только `enableRef`).
- Обработка verification statements, если присутствуют.

**Объём:** ~200–400 LOC поверх существующего emitter'а.

### 3.5 CLI tool `uhdi-to-hgdb` (Python)

Python, как и `uhdi-to-hgldd`. Converters decoupled от CIRCT, единый шаблон tooling.

Три компонента в порядке сложности.

#### (A) SQLite schema population

| hgdb table | Источник из uhdi |
|---|---|
| Instance | Рекурсивный walk `scopes[*].instantiates[]`, fresh id per instance |
| Variable | variables pool, только те, у кого есть verilog-repr entry |
| Generator Variable | variables с `bindKind == "literal"` |
| Scope Variable | variables с `ownerScopeRef == текущий scope` |
| Breakpoint | по одной row на `dbg.connect_stmt` на каждую instance host-scope'а |

Замораживаем конкретную версию hgdb (commit hash) — не гоняемся за moving target.

#### (B) Instance-path prefixing

Каждый `enable` string должен использовать имена в контексте конкретного instance'а (`top.cpu.alu.io_en` вместо `io_en`). При сериализации expression переименовываем через instance path.

#### (C) SV-string serializer

Главная сложность Phase 2. Требования:

- Precedence-aware printing по SV LRM.
- Правильные скобки (минимум, не лишние).
- Спец-синтаксис: унарные ops, `{N{x}}` replicate, `{a,b}` concat, ternary `?:`, reductions `&x` / `|x` / `^x`.

Скелет:

```python
def print_expr(expr, parent_prec=0):
    my_prec = PREC[expr.opcode]
    body = format_by_opcode(expr)
    if my_prec < parent_prec:
        return f"({body})"
    return body
```

Юнит-тесты: 50+ expression-конструкций, каждая roundtrip через Verilator `--lint-only`.

**Plan B:** всегда-скобочная стратегия `((a) + ((b) * (c)))`. Ugly output, всегда корректно.

### 3.6 Валидация Phase 2 (M2)

Набор дизайнов: 2–3 из Phase 1 + один специальный с nested when'ами.

**Метрика:** behavioral equivalence.

- Запустить simulation с hgdb-VSCode plugin.
- Поставить breakpoint на каждой source-line.
- Зафиксировать trace `(cycle, breakpoint_id_triggered)`.
- Сравнить reference (stock hgdb-Chisel-plugin) с via-uhdi traces. Должны совпадать.

**Если stock hgdb emitter недоступен:** behavioral check — debug-сессия должна subjectively работать так, как ожидается. Документируется в главе 5 как ограничение методологии.

**M2 closed:** hgdb-VSCode сессия на 2–3 дизайнах через нашу цепочку вызывает те же breakpoints, что и reference (или ведёт себя ожидаемо, если reference недоступен).

---

## 4. Текст диплома

### 4.1 Структура

Шесть глав. Суммарно ~60–80 страниц.

| Глава | Порядок | Объём |
|---|---|---|
| 1. Introduction (мотивация, цели) | идёт первым, опирается на §1 spec | ~5–8 стр |
| 2. Background (Chisel, FIRRTL, CIRCT, hgdb, Tywaves) | после intro | ~10–15 стр |
| 3. Format design (uhdi) | spec уже написан — выжимка и обоснования, пишется параллельно с началом реализации emitter'а | ~15–20 стр |
| 4. Implementation (CIRCT passes, emitter, converters) | после того как passes и emitter существуют | ~10–15 стр |
| 5. Evaluation (Tywaves + hgdb demo + ограничения) | draft после M1, финал после M2 | ~8–12 стр |
| 6. Conclusion, future work (Phase 3+ как outlook) | финальный | ~3–5 стр |

### 4.2 Тонкости

- Глава 3 (формат) — 70% уже написано в uhdi spec. **Не переписывай spec**, цитируй и фокусируйся на design decisions и их обоснованиях. Spec идёт как appendix.
- Глава 4 (implementation) — описывай **только то, что реализовано**. Phase 3+ — раздел future work.
- Глава 5 (evaluation) — screenshots Tywaves и hgdb-VSCode, сравнение traces, обсуждение ограничений. Screenshots снимаются когда код стабилен; пересъёмка после code-freeze не предполагается.
- Rejected alternatives (spec Appendix B) — материал для обоснования design decisions в главе 3.

### 4.3 Защитная speaker note

Одна страница с ключевыми тезисами:

- **Проблема:** фрагментация отладочных форматов (hgdb, HGLDD, PDG). Ни один не покрывает все use cases, lossy conversion между ними.
- **Решение:** layered unified format, consumer выбирает нужные layers.
- **Демонстрация:** один emitter из CIRCT, две независимые Python-проекции работают на одном документе.
- **Вклад:** формат (spec), два CIRCT-passes, один pool-based emitter, две projection tools.
- **Ограничения:** Phase 3+ (dataflow, temporal, provenance) — future work.

---

## 5. Сводка предположений

Уровни: **[C]** критическое — срыв = перекройка плана; **[I]** важное — срыв = лишняя работа; **[L]** слабое — локальный workaround.

| # | Предположение | Уровень | Статус |
|---|---|---|---|
| A1 | InlineInstances создаёт explicit `dbg.scope` при inlining | C | ✅ подтверждено |
| A2 | LowerTypes сохраняет `dbg.struct`/`dbg.array` | C | ✅ подтверждено |
| A3 | `dbg.variable` структурно блокирует DCE | C | ✅ подтверждено |
| A4 | `dbg.variable` допускает discardable attrs | I → L | ✅ подтверждено |
| A5 | Stable IDs стабильны между compilation runs | I | Решается через hash+counter |
| A6 | `capture-when` не конфликтует с Inliner и другими passes до ExpandWhens | I | Проверяется перед Phase 2 |
| A7 | uhdi-attributes на dbg ops переживают passes | I | Проверяется в §1.1 (см. A13) |
| A8 | FIRRTL SourceInfo сохраняется через pipeline | I | Проверяется в §1 |
| A13 | Discardable attrs (`uhdi.*`) переживают full pipeline | I | Pre-sanity (§1.1) |
| A14 | dbg dialect допускает добавление region-ops | I | Контроль над dialect в своём форке есть; форма — §1.2 |
| A15 | Tywaves корректно парсит существующий EmitUHDI output | L | Pre-sanity (§1.3) |
| A16 | Chisel `withDebug` корректно триггерит `circt_debug_*` intrinsics | I | Pre-sanity (§1.4) |

---

## 6. Emergency: тактики сокращения scope

В порядке увеличения срезанности.

### 6.1 Условия входа

- `firrtl-uhdi-init` не работает на GCD после разумной отладки.
- M1 не закрыт после реализации emitter + `uhdi-to-hgldd`.
- capture-when pass буксует несколько итераций с частичным откатом к Plan B уровней A–C (§3.3).
- Phase 2 emitter extension или SV-serializer упираются.

### 6.2 Уровень 1 — мягкие упрощения

- Memoization в capture-when не добавлять.
- SV-string serializer: always-parenthesize.
- Верификационные statements (assert/assume/cover) не поддерживать — убрать из тестовых дизайнов.
- `uhdi-to-hgldd`: убрать enum support, демо-дизайны не используют ChiselEnum.

### 6.3 Уровень 2 — сокращение демо

- Тестовый набор Phase 2 сократить до GCD + одного простого дизайна с одним when.
- Убрать SoC-дизайн с InlineInstance. Демонстрация inline только в Phase 1.
- Phase 2 evaluation: не полный behavioral trace comparison, а скриншоты работающей сессии.

### 6.4 Уровень 3 — защита только на Phase 1

- Phase 2 code — present as work-in-progress в главе 4.
- Глава 5 evaluation покрывает только Phase 1 (Tywaves).
- Глава 6 future work: Phase 2 доделка, Phase 3+ dataflow/temporal/provenance.
- Тезис переформулируется: *«формат разработан и частично реализован; реализация Tywaves-projection демонстрирует практичность pool-based архитектуры; hgdb-projection — next immediate step»*.

Это не провал. uhdi spec сам по себе — сильный thesis contribution. Phase 1 demo его валидирует.

---

## 7. Чек-лист к защите

### 7.1 Код

- [ ] `firrtl-uhdi-init` собран в CIRCT, проходит unit-tests
- [ ] `hw-uhdi-verilog-snapshot` собран, проходит unit-tests
- [ ] `export-uhdi` (pool-based, `EmitUHDI.cpp`) генерирует валидный JSON по schema на GCD/FIFO/SingleCycleCPU
- [ ] `uhdi-to-hgldd` (Python) генерирует HGLDD, открывающийся в Tywaves
- [ ] `firrtl-uhdi-capture-when` работает на nested when-тесте *(если Phase 2 входит в защиту)*
- [ ] `uhdi-to-hgdb` (Python) создаёт SQLite, открывающийся hgdb-VSCode plugin *(если Phase 2)*
- [ ] Репозиторий запушен с README, build instructions, примером

### 7.2 Текст

- [ ] Все 6 глав дописаны, пройден хотя бы один self-read
- [ ] uhdi spec приложен как appendix
- [ ] Screenshots Tywaves / hgdb-VSCode в главе 5
- [ ] Bibliography: hgdb paper, Tywaves paper, CIRCT docs, FIRRTL paper
- [ ] PDF собран, проверен на опечатки

### 7.3 Защита

- [ ] Слайды (15–20 штук)
- [ ] Видео-демо Tywaves (30–60 секунд)
- [ ] Видео-демо hgdb-VSCode *(если Phase 2)*
- [ ] Speaker notes (§4.3)
- [ ] Репетиция вслух — минимум 2 раза
- [ ] Ответы на очевидные вопросы:
  - «Почему не расширили HGLDD, а сделали новый формат?»
  - «Почему не dataflow/provenance в текущей версии?»
  - «Почему Python-конвертеры, а не CIRCT-native?»
  - «Почему не сравнение с DWARF?»

---

## 8. Первое действие

1. Прогнать четыре sanity-check'а §1 (A13 / A14 / A15 / A16).
2. Создать каркас репозитория (`src/`, `test/`, `docs/`, `scripts/`).
3. Скомпилировать CIRCT с debug symbols (`-DCMAKE_BUILD_TYPE=RelWithDebInfo`). Без этого исследовательские эксперименты — мучение.
4. Создать Overleaf/Word-шаблон диплома с заголовками глав и TOC-заготовкой. Структура должна стоять до первой страницы текста.
5. После §1 запустить Phase 1 строго по порядку: §2.2 → §2.3 → §2.4 → §2.5 → §2.6.
6. После закрытия M1 — §3 по порядку: §3.2 → §3.3 → §3.4 → §3.5 → §3.6.

**Главное:** не пытаться запрограммировать всё "правильно" с первого раза. Работающий конец цепочки важнее красивой архитектуры. Первая цель — получить хоть какой-нибудь pool-based JSON из CIRCT; остальное итеративно.

---

*— конец документа —*
