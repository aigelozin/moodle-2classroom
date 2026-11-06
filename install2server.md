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

После каждого нового входа на сервер окружение нужно включать заново:
`cd ~/moodle-2classroom && . .venv/bin/activate`.

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

Папка `backups/` и файлы `.mbz` в git не попадают.

## Шаг 6. Пробный прогон (без Google)

```bash
python migrate.py plan backups/*.mbz
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
python migrate.py auth --manual
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
> `rm token.json && python migrate.py auth --manual`.

## Шаг 9. Перенести курс

Запускайте внутри `tmux`, чтобы перенос не прервался при отключении SSH:

```bash
tmux new -s migrate
cd ~/moodle-2classroom && . .venv/bin/activate
python migrate.py run backups/курс_110.mbz 2>&1 | tee -a out/run.log
```

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

1. Откройте ссылку из вывода или https://classroom.google.com.
2. «Задания» (Classwork): темы и порядок должны совпадать с Moodle.
3. Откройте 2–3 материала: Google Doc страницы, видео, файлы.
4. Откройте один тест и пройдите его в режиме предпросмотра (значок глаза в форме).
5. Файлы курса лежат в Google Drive: `Moodle → Classroom/<название курса>`.
6. Черновики (скрытое в Moodle) видит только преподаватель. Опубликовать: «⋮» → «Изменить» → «Опубликовать».

Если нужен импорт оценок из теста: задание → удалить ссылку на форму →
«Добавить → Google Диск» → выбрать эту форму → включить «Импорт оценок».

## Шаг 11. Перенести остальные курсы

```bash
python migrate.py plan backups/*.mbz                          # сначала проверка
python migrate.py run  backups/*.mbz 2>&1 | tee -a out/run.log
```

Уже перенесённые курсы пропускаются: всё созданное записано в `out/state/`.

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
| `invalid_grant`, `Token has been expired or revoked` | `rm token.json && python migrate.py auth --manual` |
| `Mismatching state` / `Missing code` при вставке адреса | Скопирован не весь адрес — начните `auth --manual` заново и скопируйте адрес целиком |
| `HTTP 429` или `Quota exceeded` | Скрипт сам повторяет запросы; если всё равно упал — подождите час и запустите `run` снова |
| `HTTP 403 ... @ClassroomApiDisabled` | Откройте https://classroom.google.com этим Gmail, выберите роль «Я преподаватель», затем повторите |
| `@CourseStateDenied This user cannot create or transition courses` | Создайте курс вручную в Classroom («+» → «Создать курс»), скопируйте его адрес из браузера и запустите: `python migrate.py run backups/c110.mbz --course "https://classroom.google.com/c/..."` |
| `ModuleNotFoundError: googleapiclient` | Не включено окружение: `. .venv/bin/activate` |
| `Ошибка ... .xml` при `plan` | Бэкап повреждён или неполный — сделайте его в Moodle заново |

При любой другой ошибке пришлите разработчику последние 50 строк лога:
`tail -50 out/run.log`.

## Обновление проекта

```bash
cd ~/moodle-2classroom
git pull
. .venv/bin/activate && pip install -r requirements.txt
```

`credentials.json`, `token.json`, `out/` и бэкапы при обновлении не затрагиваются.
