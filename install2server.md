# Установка и запуск на сервере

Пошаговая инструкция: от чистого Linux-сервера до перенесённого курса в Google Classroom.
Примеры — для Ubuntu/Debian. На других дистрибутивах замените `apt` на свой менеджер пакетов.

Понадобится:
- Linux-сервер с доступом в интернет (к `*.googleapis.com` и `accounts.google.com`);
- аккаунт Gmail — он станет владельцем курсов в Classroom;
- резервные копии курсов Moodle (`.mbz`);
- около 30 минут на первую настройку.

---

## Шаг 1. Подготовить сервер

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip tmux
python3 --version        # нужен Python 3.9 или новее
```

`tmux` нужен, чтобы перенос продолжался, если отключится SSH.

## Шаг 2. Скачать проект

```bash
cd ~
git clone https://github.com/aigelozin/moodle-2classroom.git
cd moodle-2classroom
```

Если репозиторий приватный, а на сервере нет SSH-ключа для GitHub:

```bash
ssh-keygen -t ed25519 -C "server"      # Enter на все вопросы
cat ~/.ssh/id_ed25519.pub              # добавить в GitHub → Settings → SSH and GPG keys
git clone git@github.com:aigelozin/moodle-2classroom.git
```

## Шаг 3. Установить зависимости

```bash
cd ~/moodle-2classroom
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Включать окружение (`. .venv/bin/activate`) не обязательно: `python3 migrate.py ...`
сам переключится на `.venv`, если она есть в папке проекта.

В Ubuntu команды `python` может не быть — используйте `python3`.

## Шаг 4. Проверить, что всё работает (без Google)

```bash
python tests/make_fixture.py /tmp/test.mbz
python tests/fake_run.py /tmp/test.mbz
```

Последняя строка должна быть: `повторный запуск, новых вызовов создания: []`.

## Шаг 5. Загрузить бэкапы Moodle на сервер

В Moodle: курс → «Резервное копирование» → включить «Включить файлы» и «Банк вопросов»
→ скачать `.mbz`. Затем со своего компьютера:

```bash
ssh user@server "mkdir -p ~/moodle-2classroom/backups"
scp *.mbz user@server:~/moodle-2classroom/backups/
```

Папка `backups/` и файлы `.mbz` в git не попадают. Если бэкапов много — сразу кладите их
в `inbox/` и смотрите шаг 11 (перенос партией).

## Шаг 6. Пробный прогон (без Google)

```bash
python3 migrate.py plan backups/*.mbz
```

Для каждого курса появится отчёт `out/reports/<имя>.md`. В нём видно, какие будут темы,
материалы и тесты, что не переносится («Не переносится») и где Forms не всё поддерживает
(«Предупреждения»). Посмотреть отчёт: `less out/reports/<имя>.md`.

Если в отчёте что-то не так, пришлите его разработчику **до** переноса.

## Шаг 7. Настроить Google Cloud (один раз)

Всё делается в браузере тем Gmail, который будет владельцем курсов.

1. **Проект.** Откройте https://console.cloud.google.com → вверху выбор проекта →
   «New project» → имя, например `moodle-migration` → «Create». Убедитесь, что выбран новый проект.
2. **API.** Меню → «APIs & Services» → «Library». Найдите и для каждого нажмите «Enable»:
   - Google Classroom API
   - Google Drive API
   - Google Forms API
3. **Экран согласия.** «APIs & Services» → «OAuth consent screen» (или «Google Auth Platform» →
   «Branding»):
   - User type: **External** → «Create»;
   - App name: `Moodle migration`, support email и developer email — свой Gmail → «Save».
   - В разделе **Audience / Test users** нажмите «Add users» и добавьте свой Gmail.
     Без этого вход завершится ошибкой `access_denied`.
4. **Ключ OAuth.** «APIs & Services» → «Credentials» (или «Clients») → «Create credentials» →
   «OAuth client ID»:
   - Application type: **Desktop app**, имя любое → «Create»;
   - «Download JSON».
5. **Загрузить ключ на сервер** (со своего компьютера):

   ```bash
   scp ~/Downloads/client_secret_*.json user@server:~/moodle-2classroom/credentials.json
   ssh user@server "chmod 600 ~/moodle-2classroom/credentials.json"
   ```

## Шаг 8. Войти в Google (один раз)

```bash
cd ~/moodle-2classroom && . .venv/bin/activate
python3 migrate.py auth --manual
```

1. Скрипт покажет длинную ссылку. Откройте её в браузере на любом устройстве.
2. Войдите тем же Gmail, который добавили в Test users.
3. Google скажет «Google hasn't verified this app» → «Continue» / «Дополнительно» → «Перейти».
4. Отметьте все галочки доступа → «Continue».
5. Браузер откроет `http://localhost:8765/?state=…&code=…` и покажет ошибку
   «Не удаётся получить доступ к сайту». Это нормально.
6. Скопируйте **весь** адрес из адресной строки, вставьте в терминал и нажмите Enter.

Ответ `Готово, токен сохранён в token.json` означает, что вход выполнен.
`token.json` — это доступ к вашему Google. Не отправляйте его никому и не коммитьте
(в `.gitignore` он уже исключён).

> Пока приложение в режиме «Testing», Google отзывает токен примерно через 7 дней.
> Если позже появится `invalid_grant` или `Token has been expired or revoked`, выполните:
> `rm token.json && python3 migrate.py auth --manual`.

## Шаг 9. Перенести курс

Запускайте внутри `tmux`, чтобы перенос не прервался при отключении SSH:

```bash
tmux new -s migrate
cd ~/moodle-2classroom && . .venv/bin/activate
python3 migrate.py run backups/курс_110.mbz
```

Весь вывод сам сохраняется в `out/logs/run-<дата-время>.log`.

- Отключиться, не останавливая перенос: `Ctrl+B`, затем `D`.
- Вернуться: `tmux attach -t migrate`.

Курс на 90 элементов и 36 тестов переносится примерно за 10–30 минут.
В конце будет строка:

```
✓ перенесено: https://classroom.google.com/c/…  (id …)
```

Опции:

| Опция | Что делает |
|---|---|
| `--skip-hidden` | не переносить скрытое в Moodle (по умолчанию — переносится черновиками) |
| `--private-forms` | не открывать тесты по ссылке для всех |
| `--out ДРУГАЯ_ПАПКА` | куда класть отчёты и state |

## Шаг 10. Проверить результат

Ссылки на все перенесённые курсы записываются в `out/courses.md`:

```bash
python3 migrate.py list
```

1. Откройте ссылку из `out/courses.md` или https://classroom.google.com.
2. «Задания» (Classwork): темы и порядок должны совпадать с Moodle.
3. Откройте 2–3 материала: Google Doc страницы, видео, файлы.
4. Откройте один тест и пройдите его в режиме предпросмотра (значок глаза в форме).
5. Файлы курса лежат в Google Drive: `Moodle → Classroom/<название курса>`.
6. Черновики (скрытое в Moodle) видит только преподаватель. Опубликовать: «⋮» → «Изменить» → «Опубликовать».

Если нужен импорт оценок из теста: задание → удалить ссылку на форму →
«Добавить → Google Диск» → выбрать эту форму → включить «Импорт оценок».

## Шаг 11. Перенести остальные курсы партией

Не нужно запускать каждый бэкап отдельно. Сложите все бэкапы в папку `inbox/`
и запустите одну команду — курсы создадутся по очереди.

**1. Загрузить бэкапы на сервер одной командой** (со своего компьютера, из папки с бэкапами):

```bash
ssh user@server "mkdir -p ~/moodle-2classroom/inbox"
rsync -av --progress *.mbz user@server:~/moodle-2classroom/inbox/
```

`rsync` докачивает оборванные файлы и не копирует повторно уже загруженные.
Если `rsync` нет — подойдёт `scp *.mbz user@server:~/moodle-2classroom/inbox/`.

**2. Проверка всей партии без Google** (секунды на курс):

```bash
python3 migrate.py plan inbox/
```

В конце — сводка: какие бэкапы разобрались, какие повреждены. Отчёты — в `out/reports/`.

**3. Перенос партии** (внутри `tmux`, см. шаг 9):

```bash
python3 migrate.py batch
```

Что происходит:
- бэкапы из `inbox/` обрабатываются по очереди, в алфавитном порядке; в выводе видно `[3/12]`;
- перенесённый бэкап перекладывается в `inbox/done/`, так что в `inbox/` остаётся только то,
  что ещё не перенесено;
- ошибка в одном курсе не останавливает остальные; курсы с ошибкой в конце партии
  повторяются ещё раз (`--retries 2` — два повтора, `--retries 0` — без повторов);
- повреждённые бэкапы не повторяются и остаются в `inbox/` — их нужно скачать из Moodle заново;
- уже перенесённые курсы пропускаются, даже если бэкап переименовали или переложили в другую папку;
- весь вывод автоматически пишется в `out/logs/batch-<дата-время>.log` (`| tee` не нужен);
- в конце — сводка по каждому бэкапу и обновлённый `out/courses.md` со ссылками.

Пример сводки:

```
============================================================
Итог (batch): ✓ перенесён: 10, • пропущен: 1, ✗ повреждён: 1
  ✓ перенесён  c001_Курс_йоги_000.mbz
  ...
  ✗ повреждён  c050_Курс_йоги_116.mbz
               бэкап повреждён или это не .mbz — скачайте его из Moodle заново
```

Добавили новые бэкапы в `inbox/` — просто запустите `python3 migrate.py batch` снова.

Опции `batch`:

| Опция | Что делает |
|---|---|
| `python3 migrate.py batch другая/папка` | брать бэкапы не из `inbox/`, а из указанной папки |
| `--keep` | не перекладывать готовые бэкапы в `done/` |
| `--retries N` | сколько раз повторять курсы с ошибкой (по умолчанию 1) |
| `--recheck` | не пропускать перенесённые курсы, а проверить их заново (дублей не будет) |
| `--skip-hidden`, `--private-forms` | как у `run` |

`run` тоже принимает папку и несколько файлов (`python3 migrate.py run backups/`), но не
перекладывает бэкапы в `done/` и не повторяет упавшие.

### Сделать бэкапы в Moodle пачкой (если есть доступ к серверу Moodle)

Вместо скачивания по одному через интерфейс — на сервере Moodle, из его папки:

```bash
mkdir -p /tmp/mbz
for id in 2 3 5 7 84; do        # id курсов: видны в адресе course/view.php?id=…
  sudo -u www-data php admin/cli/backup.php --courseid=$id --destination=/tmp/mbz
done
```

Затем перенесите `/tmp/mbz/*.mbz` в `inbox/` сервера переноса (`rsync`, как выше).
Пользователь веб-сервера может называться иначе (`apache`, `nginx`). Бэкап делается
с настройками резервного копирования по умолчанию — проверьте в Moodle: «Администрирование →
Курсы → Резервное копирование → Общие настройки резервного копирования», что включены
файлы и банк вопросов.

## Если что-то пошло не так

**Перенос оборвался** (сеть, лимиты Google, перезагрузка) — запустите ту же команду `run`
ещё раз. Он продолжит с места остановки и не создаст дублей.

**Перенести курс заново с нуля:**
1. В Classroom: курс → «⋮» → «Архивировать» → в архиве «⋮» → «Удалить».
2. В Drive удалите папку курса.
3. На сервере удалите state курса: `ls out/state/`, затем `rm out/state/<курс>-*.json`.
4. Снова запустите `run`.

**Частые ошибки:**

| Ошибка | Что делать |
|---|---|
| `Нет credentials.json` | Шаг 7, пункты 4–5 |
| `access_denied` при входе | Добавьте свой Gmail в Test users (шаг 7, п. 3) |
| `... API has not been used in project ... or it is disabled` | Включите это API (шаг 7, п. 2), подождите 2–3 минуты |
| `invalid_grant`, `Token has been expired or revoked` | `rm token.json && python3 migrate.py auth --manual` |
| `Mismatching state` / `Missing code` при вставке адреса | Скопирован не весь адрес — начните `auth --manual` заново и скопируйте адрес целиком |
| `HTTP 429` или `Quota exceeded` | Скрипт сам повторяет запросы; если всё равно упал — подождите час и запустите `run` снова |
| `HTTP 403 ... @ClassroomApiDisabled` | Откройте https://classroom.google.com этим Gmail, выберите роль «Я преподаватель», затем повторите |
| `@CourseStateDenied This user cannot create or transition courses` | Создайте курс вручную в Classroom («+» → «Создать курс»), скопируйте его адрес из браузера и запустите: `python3 migrate.py run backups/c110.mbz --course "https://classroom.google.com/c/..."` |
| `! Classroom отклонил «…» — ищу проблемное вложение` | Не ошибка: скрипт сам найдёт вложение, которое Google не принимает (часто — недоступное или приватное видео), и вставит его ссылкой в текст материала. Список таких мест — в `out/courses.md`, раздел «Замечания» |
| `✗ «…»: … — пропущен` | Элемент не создался, остальные перенесены. Запустите `run` ещё раз; если повторяется — пришлите строку разработчику |
| `ModuleNotFoundError: No module named 'google'` / `googleapiclient` | Не установлены зависимости: `cd ~/moodle-2classroom && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` |
| `Команда «python» не найдена` | Пишите `python3` вместо `python` |
| `Ошибка ... .xml` при `plan` | Бэкап повреждён или неполный — сделайте его в Moodle заново |

При любой другой ошибке пришлите разработчику последние 50 строк лога:
`tail -50 "$(ls -t out/logs/*.log | head -1)"`.

## Обновление проекта

```bash
cd ~/moodle-2classroom
git pull
. .venv/bin/activate && pip install -r requirements.txt
```

`credentials.json`, `token.json`, `out/` и бэкапы при обновлении не затрагиваются.
