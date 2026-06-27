# Skill: Создание SPICE-моделей для Pulsar

## Структура библиотеки Pulsar

~/Pulsar/Mod/
 opamp/      # Операционные усилители (формат: 5 выводов)
 Diodes/     # Диоды
 BJT/        # Биполярные транзисторы
 MOSFET/     # Полевые транзисторы

Каждый компонент хранится в отдельном файле с именем компонента:
- ~/Pulsar/Mod/opamp/LM358.lib
- ~/Pulsar/Mod/Diodes/1N4148.lib
- ~/Pulsar/Mod/BJT/BC547.lib
- ~/Pulsar/Mod/MOSFET/BS250.lib

## Формат моделей

### Операционные усилители (opamp/)
Обязательно 5 выводов: in+, in-, VCC+, VCC-, OUT

* [НАЗВАНИЕ] Operational Amplifier Macro Model
* connections: N+ N- V+ V- OUT
* Параметры: GBW=X МГц, SR=X В/мкс, Rin=X МОм
* Создано: [дата]
.SUBCKT [НАЗВАНИЕ] 1 2 3 4 5
[код модели]
.ENDS [НАЗВАНИЕ]

Типовая структура модели ОУ:
1. Входное сопротивление (Rin)
2. Дифференциальный усилитель с крутизной (G-источник)
3. Доминантный полюс (RC-цепь)
4. Ограничение по питанию (диоды)
5. Выходной буфер (E-источник + Rout)

### Диоды (Diodes/)
* [НАЗВАНИЕ] Diode Model
.MODEL [НАЗВАНИЕ] D(IS=X RS=X N=X CJO=X VJ=X TT=X)

### Биполярные транзисторы (BJT/)
* [НАЗВАНИЕ] BJT Model (NPN/PNP)
.MODEL [НАЗВАНИЕ] NPN/PNP(IS=X BF=X VAF=X ...)

### Полевые транзисторы (MOSFET)
* [НАЗВАНИЕ] MOSFET models (NMOS/PMOS)

## Процесс создания модели

### Шаг 1: Анализ datasheet
Используй websearch для поиска параметров:
- ОУ: GBW, slew rate, Rin, Rout, Voffset, CMRR
- Диоды: Is, Rs, N, Cjo, Vj, Tt
- BJT: Is, Bf, Vaf, Ikf, Cjc, Cje

### Шаг 2: Создание модели
Используй spicebridge_create_model с извлечёнными параметрами.

### Шаг 3: Валидация
Обязательные тесты для ОУ:

* Тестовая схема: инвертирующий усилитель Ku=10
V1 in 0 AC 1 SIN(0 0.1 1k)
R1 in n1 1k
R2 n1 out 10k
X1 n1 0 VCC VEE out [НАЗВАНИЕ]
VCC VCC 0 15
VEE VEE 0 -15

.ac dec 100 1 10Meg
.tran 1u 10m
.end

Проверить:
- Полоса пропускания ≈ GBW/Ku
- Slew rate соответствует datasheet
- Правильное насыщение выхода
- Модель сходится без ошибок

### Шаг 4: Запись в библиотеку
Используй write для создания файла:
- ОУ → ~/Pulsar/Mod/opamp/[НАЗВАНИЕ].lib
- Диоды → ~/Pulsar/Mod/Diodes/[НАЗВАНИЕ].lib
- BJT → ~/Pulsar/Mod/BJT/[НАЗВАНИЕ].lib

### Шаг 5: Документирование
Обнови ~/Pulsar/Mod/README.md (если существует) с информацией о новой модели.

## Инструменты SPICEBridge
- spicebridge_create_model — создание модели
- spicebridge_run_ac — тест АЧХ
- spicebridge_run_transient — тест переходных процессов
- spicebridge_validate_netlist — проверка синтаксиса
- spicebridge_compare_specs — сравнение с datasheet

## Критерии качества
- Модель сходится в ngspice без ошибок
- Параметры соответствуют datasheet (±10%)
- Правильное насыщение выхода
- Реалистичные slew rate и GBW
- Файл имеет понятные комментарии
