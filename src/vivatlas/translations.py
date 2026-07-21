"""Interface strings in three languages: en (source), ru, he.

Keys are grouped by section with dots. English is required (it's also the
fallback); Russian and Hebrew where possible. Placeholders in curly braces are
substituted via str.format: t("foot.counts", cards=10, tags=3).

Hebrew is right-to-left — direction is set by i18n/template via dir, the text
itself here is plain. Grows as templates get translated.
"""

CATALOG: dict[str, dict[str, str]] = {
    # --- languages/switcher ---------------------------------------------------
    "lang.label": {"en": "Language", "ru": "Язык", "he": "שפה"},
    # --- shared shell (base.html) ---------------------------------------------
    "nav.menu": {"en": "Menu", "ru": "Меню", "he": "תפריט"},
    "nav.add": {"en": "Add", "ru": "Добавить", "he": "הוספה"},
    "nav.add_tool": {"en": "Add tool", "ru": "Добавить инструмент", "he": "הוספת כלי"},
    "nav.catalog": {"en": "Catalogue", "ru": "Каталог", "he": "קטלוג"},
    "nav.favorites": {"en": "Favourites", "ru": "Избранное", "he": "מועדפים"},
    "nav.drafts": {"en": "Drafts", "ru": "Черновики", "he": "טיוטות"},
    "nav.changes": {"en": "Changes", "ru": "Изменения", "he": "שינויים"},
    "nav.help": {"en": "Help", "ru": "Помощь", "he": "עזרה"},
    "nav.settings": {"en": "Settings", "ru": "Настройки", "he": "הגדרות"},
    "nav.admin": {"en": "Administration", "ru": "Администрирование", "he": "ניהול"},
    "nav.logout": {"en": "Log out", "ru": "Выйти", "he": "התנתקות"},
    "nav.sections": {"en": "Sections", "ru": "Разделы", "he": "מדורים"},
    "skip.content": {"en": "Skip to content", "ru": "К содержимому", "he": "דלג לתוכן"},
    "catalog.h1": {"en": "Tool catalogue", "ru": "Каталог инструментов", "he": "קטלוג הכלים"},
    "side.no_folders_hint": {
        "en": "No folders yet. Create them in {settings} — then you can drag cards here.",
        "ru": "Папок пока нет. Заведите их в {settings} — потом сюда можно перетаскивать карточки.",
        "he": "עדיין אין תיקיות. צרו אותן ב{settings} — ואז אפשר לגרור לכאן כרטיסים.",
    },
    "side.settings_word": {"en": "settings", "ru": "настройках", "he": "הגדרות"},
    "foot.counts": {
        "en": "{cards} cards · {tags} tags",
        "ru": "{cards} карточек · {tags} тегов",
        "he": "{cards} כרטיסים · {tags} תגיות",
    },
    "side.card_counts": {
        "en": "My cards: {mine} · Total: {total}",
        "ru": "Мои карточки: {mine} · Всего: {total}",
        "he": "הכרטיסים שלי: {mine} · סה״כ: {total}",
    },
    "account.aria": {"en": "Account: {name}", "ru": "Аккаунт: {name}", "he": "חשבון: {name}"},
    "account.type_admin": {"en": "Administrator", "ru": "Администратор", "he": "מנהל"},
    "account.type_member": {"en": "Member", "ru": "Участник", "he": "חבר"},
    # --- theme ----------------------------------------------------------------
    "theme.label": {"en": "Theme", "ru": "Тема", "he": "ערכת נושא"},
    "theme.menu_aria": {"en": "Colour theme", "ru": "Тема оформления", "he": "ערכת צבעים"},
    "theme.light": {"en": "Light", "ru": "Светлая", "he": "בהיר"},
    "theme.dark": {"en": "Dark", "ru": "Тёмная", "he": "כהה"},
    "theme.oled": {"en": "OLED", "ru": "OLED", "he": "OLED"},
    "theme.system": {"en": "System", "ru": "Как в системе", "he": "כמו במערכת"},
    # --- sign-in/door (auth.html) ---------------------------------------------
    "auth.email": {"en": "Email", "ru": "Почта", "he": "דוא\"ל"},
    "auth.password": {"en": "Password", "ru": "Пароль", "he": "סיסמה"},
    "auth.password_again": {"en": "Password again", "ru": "Пароль ещё раз", "he": "הסיסמה שוב"},
    "auth.password_note": {
        "en": "At least 12 characters. A long phrase is safer than a short jumble and easier to remember.",
        "ru": "Пароль от 12 знаков. Длинная фраза надёжнее короткой мешанины и запоминается легче.",
        "he": "לפחות 12 תווים. משפט ארוך בטוח יותר מערבוב קצר וקל יותר לזכור.",
    },
    "auth.setup.title": {"en": "First sign-in", "ru": "Первый вход", "he": "כניסה ראשונה"},
    "auth.setup.lede": {
        "en": "No one has set up the program yet. Whoever creates an account now becomes the owner: only they decide whether to let others in.",
        "ru": "Программу ещё никто не настроил. Кто заведёт аккаунт сейчас — станет хозяином: только он решает, пускать ли других.",
        "he": "אף אחד עדיין לא הגדיר את התוכנה. מי שייצור חשבון עכשיו יהפוך לבעלים: רק הוא מחליט אם לתת לאחרים להיכנס.",
    },
    "auth.setup.name": {"en": "Your name", "ru": "Как вас звать", "he": "השם שלך"},
    "auth.setup.submit": {"en": "Create owner", "ru": "Завести хозяина", "he": "יצירת בעלים"},
    "auth.login.title": {"en": "Sign in", "ru": "Вход", "he": "כניסה"},
    "auth.login.submit": {"en": "Sign in", "ru": "Войти", "he": "היכנס"},
    "auth.forgot_link": {"en": "Forgot your password?", "ru": "Забыли пароль?", "he": "שכחת סיסמה?"},
    "auth.totp.title": {"en": "Second code", "ru": "Второй код", "he": "קוד שני"},
    "auth.totp.app": {
        "en": "Open the app and enter the six-digit code.",
        "ru": "Откройте приложение и введите шестизначный код.",
        "he": "פתחו את האפליקציה והזינו את הקוד בן שש הספרות.",
    },
    "auth.totp.backup": {
        "en": "Enter one of your backup codes.",
        "ru": "Введите один из кодов восстановления.",
        "he": "הזינו אחד מקודי השחזור.",
    },
    "auth.totp.code_app": {"en": "Code from the app", "ru": "Код из приложения", "he": "קוד מהאפליקציה"},
    "auth.totp.code_backup": {"en": "Backup code", "ru": "Код восстановления", "he": "קוד שחזור"},
    "auth.totp.confirm": {"en": "Confirm", "ru": "Подтвердить", "he": "אישור"},
    "auth.totp.to_backup": {
        "en": "No phone? Enter a backup code",
        "ru": "Нет телефона? Ввести код восстановления",
        "he": "אין טלפון? הזינו קוד שחזור",
    },
    "auth.totp.to_app": {
        "en": "← enter the code from the app",
        "ru": "← ввести код из приложения",
        "he": "← הזינו את הקוד מהאפליקציה",
    },
    "auth.forgot.title": {"en": "Reset password", "ru": "Сброс пароля", "he": "איפוס סיסמה"},
    "auth.forgot.lede": {
        "en": "Enter the email your account uses. We'll send a link to change the password — it's valid for an hour.",
        "ru": "Впишите почту, на которую заведён аккаунт. Пришлём ссылку на смену пароля — она действует час.",
        "he": "הזינו את הדוא\"ל שאליו רשום החשבון. נשלח קישור לשינוי הסיסמה — הוא תקף לשעה.",
    },
    "auth.forgot.submit": {"en": "Send link", "ru": "Прислать ссылку", "he": "שליחת קישור"},
    "auth.back_to_login": {"en": "← to sign-in", "ru": "← ко входу", "he": "← לכניסה"},
    "auth.sent.title": {"en": "Check your email", "ru": "Проверьте почту", "he": "בדקו את הדוא\"ל"},
    "auth.sent.lede": {
        "en": "If that email has an account, a password-reset link has been sent to it. The link is valid for an hour and works once.",
        "ru": "Если такая почта заведена, на неё ушло письмо со ссылкой на смену пароля. Ссылка действует час и срабатывает один раз.",
        "he": "אם קיים חשבון לדוא\"ל הזה, נשלח אליו קישור לשינוי הסיסמה. הקישור תקף לשעה ופועל פעם אחת.",
    },
    "auth.sent.note": {
        "en": "No email? Check spam, or try later — mail may not be set up by the administrator yet.",
        "ru": "Письма нет? Загляните в спам или попробуйте позже — почта могла быть ещё не настроена администратором.",
        "he": "אין הודעה? בדקו בספאם או נסו מאוחר יותר — ייתכן שהדוא\"ל עדיין לא הוגדר על ידי המנהל.",
    },
    "auth.reset.title": {"en": "New password", "ru": "Новый пароль", "he": "סיסמה חדשה"},
    "auth.reset.lede": {
        "en": "Choose a new password. After the change, all open sessions close — you'll need to sign in again.",
        "ru": "Придумайте новый пароль. После смены все открытые сессии закроются — войти нужно будет заново.",
        "he": "בחרו סיסמה חדשה. לאחר השינוי כל ההתחברויות הפתוחות ייסגרו — יהיה צורך להיכנס מחדש.",
    },
    "auth.reset.new": {"en": "New password", "ru": "Новый пароль", "he": "סיסמה חדשה"},
    "auth.reset.submit": {"en": "Change password", "ru": "Сменить пароль", "he": "שינוי סיסמה"},
    "auth.reset_bad.title": {"en": "Link is not valid", "ru": "Ссылка не годится", "he": "הקישור אינו תקף"},
    "auth.reset_bad.lede": {
        "en": "The password-reset link has expired, was already used, or was mistyped. Request a new one — the old link no longer works.",
        "ru": "Ссылка на смену пароля устарела, уже сработала или набрана с ошибкой. Запросите новую — старая больше не действует.",
        "he": "הקישור לשינוי הסיסמה פג, כבר נוצל, או הוקלד בטעות. בקשו קישור חדש — הישן כבר אינו פועל.",
    },
    "auth.reset_bad.link": {
        "en": "Request a new link",
        "ru": "Запросить новую ссылку",
        "he": "בקשת קישור חדש",
    },
    "auth.reset_done.title": {"en": "Password changed", "ru": "Пароль сменён", "he": "הסיסמה שונתה"},
    "auth.reset_done.lede": {
        "en": "Done. Sign in with the new password.",
        "ru": "Готово. Войдите с новым паролем.",
        "he": "הושלם. היכנסו עם הסיסמה החדשה.",
    },
    "auth.reset_done.link": {"en": "Sign in", "ru": "Войти", "he": "כניסה"},
    # --- strings from inline-JS + small gaps (edited by hand, not fan-out) -----
    "common.yes": {"en": "Yes", "ru": "Да", "he": "כן"},
    "common.no": {"en": "No", "ru": "Нет", "he": "לא"},
    "common.cancel": {"en": "Cancel", "ru": "Отмена", "he": "ביטול"},
    "common.save": {"en": "Save", "ru": "Сохранить", "he": "שמירה"},
    "index.type_aria": {"en": "Type", "ru": "Тип", "he": "סוג"},
    "settings.js_scanning": {"en": "Scanning…", "ru": "Сканирую…", "he": "סורק…"},
    "settings.js_expand": {"en": "Expand section", "ru": "Развернуть раздел", "he": "הרחבת מדור"},
    "settings.js_collapse": {"en": "Collapse section", "ru": "Свернуть раздел", "he": "צמצום מדור"},
    "settings.js_confirm_save": {
        "en": "Save settings and exit?",
        "ru": "Сохранить настройки и выйти?",
        "he": "לשמור הגדרות ולצאת?",
    },
    "settings.js_confirm_exit": {
        "en": "Exit without saving changes?",
        "ru": "Выйти без сохранения изменений?",
        "he": "לצאת בלי לשמור שינויים?",
    },
    "add.js_working": {"en": "Working…", "ru": "Работаю…", "he": "עובד…"},
    "artifact.js_copied": {"en": "Copied", "ru": "Скопировано", "he": "הועתק"},
    "logo.home_alt": {
        "en": "VivAtlas — home",
        "ru": "VivAtlas — на главную",
        "he": "VivAtlas — לדף הבית",
    },
    "modal.window_title": {"en": "Window", "ru": "Окно", "he": "חלון"},
    # --- type labels (were web.TYPE_NAMES) ------------------------------------
    "type.design-kit": {"en": "Design kit", "ru": "дизайн-набор", "he": "ערכת עיצוב"},
    "type.claude-skill": {"en": "Claude skill", "ru": "скилл Claude", "he": "מיומנות Claude"},
    "type.skill": {"en": "Skill", "ru": "скилл", "he": "מיומנות"},
    "type.claude-command": {"en": "Command", "ru": "команда", "he": "פקודה"},
    "type.claude-agent": {"en": "Agent", "ru": "агент", "he": "סוכן"},
    "type.mcp-server": {"en": "MCP server", "ru": "MCP-сервер", "he": "שרת MCP"},
    "type.plugin": {"en": "Plugin", "ru": "плагин", "he": "תוסף"},
    "type.project": {"en": "Project", "ru": "проект", "he": "פרויקט"},
    "type.draft": {"en": "Draft", "ru": "черновик", "he": "טיוטה"},
    "type.unknown": {"en": "Unrecognized", "ru": "не опознан", "he": "לא זוהה"},
    # --- recognition basis (were web.BASIS_NAMES) -----------------------------
    "basis.documentation": {"en": "stated in the description", "ru": "прямо сказано в описании", "he": "נאמר במפורש בתיאור"},
    "basis.tags": {"en": "inferred from tags", "ru": "выведено по тегам", "he": "הוסק מהתגיות"},
    "basis.usage": {"en": "from usage history", "ru": "по истории использования", "he": "לפי היסטוריית השימוש"},
    "basis.ai-inference": {"en": "guessed by meaning", "ru": "догадка по смыслу", "he": "ניחוש לפי המשמעות"},
    # --- source state (were upstream.STATUS_NAMES) ----------------------------
    "status.in-sync": {"en": "matches the source", "ru": "совпадает с источником", "he": "תואם למקור"},
    "status.update-available": {"en": "new version available", "ru": "вышла новая версия", "he": "יצאה גרסה חדשה"},
    "status.locally-modified": {"en": "you edited it — can't update", "ru": "вы правили — обновлять нельзя", "he": "ערכת — אי אפשר לעדכן"},
    "status.diverged": {"en": "diverged on both sides", "ru": "разошлось с обеих сторон", "he": "התפצל משני הצדדים"},
    "status.unknown": {"en": "nothing to compare with", "ru": "сравнить не с чем", "he": "אין עם מה להשוות"},
    # --- change kind (were changes.KIND_NAMES; marks are neutral) -------------
    "kind.added": {"en": "appeared", "ru": "появилось", "he": "נוסף"},
    "kind.updated": {"en": "changed", "ru": "изменилось", "he": "השתנה"},
    "kind.removed": {"en": "gone", "ru": "пропало", "he": "הוסר"},
    "kind.renamed": {"en": "renamed", "ru": "переименовано", "he": "שמו שונה"},
    # --- purposes (purposes.py; key is English, label was Russian) ------------
    "purpose.security": {"en": "security", "ru": "безопасность", "he": "אבטחה"},
    "purpose.accessibility": {"en": "accessibility", "ru": "доступность", "he": "נגישות"},
    "purpose.performance": {"en": "performance", "ru": "скорость", "he": "ביצועים"},
    "purpose.testing": {"en": "testing", "ru": "проверка", "he": "בדיקות"},
    "purpose.research": {"en": "research", "ru": "исследование", "he": "מחקר"},
    "purpose.code": {"en": "code", "ru": "код", "he": "קוד"},
    "purpose.automation": {"en": "automation", "ru": "автоматизация", "he": "אוטומציה"},
    "purpose.design": {"en": "design", "ru": "оформление", "he": "עיצוב"},
    "purpose.unknown": {"en": "undetermined", "ru": "не определено", "he": "לא נקבע"},
    # --- update periods (filters.PERIODS) -------------------------------------
    "period.7": {"en": "past week", "ru": "за неделю", "he": "בשבוע האחרון"},
    "period.30": {"en": "past month", "ru": "за месяц", "he": "בחודש האחרון"},
    "period.90": {"en": "past three months", "ru": "за три месяца", "he": "בשלושת החודשים האחרונים"},
    # --- tag categories (filters.CATEGORY_ORDER; key is the canonical slug) ----
    "tagcat.purpose": {"en": "Purpose", "ru": "назначение", "he": "מטרה"},
    "tagcat.platform": {"en": "Platform", "ru": "платформа", "he": "פלטפורמה"},
    "tagcat.language": {"en": "Language", "ru": "язык", "he": "שפה"},
    "tagcat.format": {"en": "Format", "ru": "формат", "he": "פורמט"},
    "tagcat.runtime": {"en": "Runtime", "ru": "запуск", "he": "הרצה"},
    "tagcat.type": {"en": "Type", "ru": "тип", "he": "סוג"},
    "tagcat.other": {"en": "Other", "ru": "прочее", "he": "אחר"},
    # --- sign-in/password error messages (functions return keys) --------------
    "auth.err.bad_credentials": {
        "en": "Invalid email or password.",
        "ru": "Неверная почта или пароль.",
        "he": "דוא\"ל או סיסמה שגויים.",
    },
    "auth.err.locked": {
        "en": "Too many attempts. Try again in {minutes} min.",
        "ru": "Слишком много попыток. Попробуйте через {minutes} мин.",
        "he": "יותר מדי ניסיונות. נסו שוב בעוד {minutes} דק'.",
    },
    "auth.err.email_invalid": {
        "en": "Enter a real email — the password reset is sent to it.",
        "ru": "Впишите настоящую почту — на неё пойдёт сброс пароля.",
        "he": "הזינו דוא\"ל אמיתי — אליו יישלח איפוס הסיסמה.",
    },
    "auth.err.pw_mismatch": {
        "en": "Passwords don't match.",
        "ru": "Пароли не совпадают.",
        "he": "הסיסמאות אינן תואמות.",
    },
    "auth.err.totp_bad": {
        "en": "The code didn't match. Check it and try again.",
        "ru": "Код не подошёл. Проверьте и попробуйте ещё раз.",
        "he": "הקוד לא התאים. בדקו ונסו שוב.",
    },
    "err.pw_short": {
        "en": "Password shorter than 12 characters. Length protects better than special characters.",
        "ru": "Пароль короче 12 знаков. Длина защищает лучше, чем спецсимволы.",
        "he": "הסיסמה קצרה מ-12 תווים. אורך מגן טוב יותר מתווים מיוחדים.",
    },
    "err.pw_long": {
        "en": "Password longer than 1024 bytes.",
        "ru": "Пароль длиннее 1024 байт.",
        "he": "הסיסמה ארוכה מ-1024 בתים.",
    },
    "err.pw_common": {
        "en": "This password is first on cracking lists.",
        "ru": "Такой пароль стоит в списках для перебора первым.",
        "he": "סיסמה כזו נמצאת ראשונה ברשימות הפריצה.",
    },
    # --- form notifications (admin/settings) ----------------------------------
    "admin.smtp.saved": {
        "en": "Mail settings saved.",
        "ru": "Настройки почты сохранены.",
        "he": "הגדרות הדוא\"ל נשמרו.",
    },
    "admin.smtp.fill_first": {
        "en": "First enter the SMTP host and return address, then save.",
        "ru": "Сначала впишите узел SMTP и обратный адрес, потом сохраните.",
        "he": "תחילה הזינו את שרת ה-SMTP וכתובת השולח, ואז שמרו.",
    },
    "admin.smtp.test_sent": {
        "en": "Test email sent to {to}.",
        "ru": "Проверочное письмо отправлено на {to}.",
        "he": "הודעת בדיקה נשלחה אל {to}.",
    },
    "settings.src.pick_host": {
        "en": "Choose a host: GitHub or Gitea.",
        "ru": "Выберите хостинг: GitHub или Gitea.",
        "he": "בחרו מארח: GitHub או Gitea.",
    },
    "settings.src.bad_url": {
        "en": "The address must start with http:// or https:// — check the link.",
        "ru": "Адрес должен начинаться с http:// или https:// — проверьте ссылку.",
        "he": "הכתובת חייבת להתחיל ב-http:// או https:// — בדקו את הקישור.",
    },
    # --- access denials and "not found" (HTTPException.detail) ----------------
    "err.login_required": {"en": "Please sign in.", "ru": "нужно войти", "he": "יש להתחבר."},
    "err.artifact_not_found": {
        "en": "Item not found.",
        "ru": "карточка не найдена",
        "he": "הפריט לא נמצא.",
    },
    "err.source_not_found": {
        "en": "Source not found.",
        "ru": "источник не найден",
        "he": "המקור לא נמצא.",
    },
    "err.user_not_found": {
        "en": "User not found.",
        "ru": "пользователь не найден",
        "he": "המשתמש לא נמצא.",
    },
    "err.owner_only_section": {
        "en": "This section is the owner's.",
        "ru": "это раздел владельца",
        "he": "מדור זה שמור לבעלים.",
    },
    "err.categories_owner_only": {
        "en": "Categories are managed by the owner.",
        "ru": "категориями управляет владелец",
        "he": "הקטגוריות מנוהלות על ידי הבעלים.",
    },
    "err.categorize_owner_only": {
        "en": "Only the owner can file items into categories.",
        "ru": "раскладывать по категориям может владелец",
        "he": "רק הבעלים יכול לשייך פריטים לקטגוריות.",
    },
    "err.share_owner_only": {
        "en": "Only the owner can share an item.",
        "ru": "выложить в общее может только владелец",
        "he": "רק הבעלים יכול לשתף פריט.",
    },
    "err.unshare_owner_or_admin": {
        "en": "Only the owner or an admin can unshare.",
        "ru": "снять с общего может владелец или администратор",
        "he": "רק הבעלים או מנהל יכול לבטל שיתוף.",
    },
    "err.delete_owner_or_admin": {
        "en": "Only the owner can delete; an admin can remove shared items only.",
        "ru": "удалить может владелец, а администратор — только общую",
        "he": "רק הבעלים יכול למחוק; מנהל יכול להסיר פריטים משותפים בלבד.",
    },
    "err.cant_disable_self": {
        "en": "You can't disable yourself.",
        "ru": "нельзя выключить самого себя",
        "he": "אי אפשר להשבית את עצמך.",
    },
    "err.last_owner": {
        "en": "This is the last owner — can't disable.",
        "ru": "это последний владелец — не выключить",
        "he": "זהו הבעלים האחרון — אי אפשר להשבית.",
    },
    # --- source scan (precheck and background) --------------------------------
    "scan.err.source_not_found": {
        "en": "Source not found.",
        "ru": "Источник не найден.",
        "he": "המקור לא נמצא.",
    },
    "scan.err.gitea_only": {
        "en": "Scanning currently supports only Gitea and Codeberg. If this is your Gitea — choose “Gitea” in the host list on the left and press “Save”, then “Scan”. Providers for GitHub and others will come later.",
        "ru": "Скан пока умеет только Gitea и Codeberg. Если это ваш Gitea — выберите «Gitea» в списке хостингов слева и нажмите «Сохранить», затем «Сканировать». Провайдеры для GitHub и других добавим позже.",
        "he": "כרגע הסריקה תומכת רק ב-Gitea וב-Codeberg. אם זה ה-Gitea שלכם — בחרו ‏«Gitea» ברשימת המארחים משמאל ולחצו «שמירה», ואז «סריקה». ספקים ל-GitHub ולאחרים יתווספו בהמשך.",
    },
    "scan.err.no_token": {
        "en": "The source has no token — enter an access token and save.",
        "ru": "У источника нет токена — впишите токен доступа и сохраните.",
        "he": "למקור אין אסימון — הזינו אסימון גישה ושמרו.",
    },
    "scan.err.token_unreadable": {
        "en": "The token is unreadable (the encryption key changed) — enter it again.",
        "ru": "Токен нечитаем (сменился ключ шифрования) — впишите его заново.",
        "he": "האסימון אינו קריא (מפתח ההצפנה השתנה) — הזינו אותו מחדש.",
    },
    "scan.err.token_lost": {
        "en": "The token became unreadable.",
        "ru": "Токен стал нечитаем.",
        "he": "האסימון הפך לבלתי קריא.",
    },
    # --- adding a tool (add steps) --------------------------------------------
    "add.err.file_too_big": {
        "en": "The file is larger than {mb} MB — the model won't accept it.",
        "ru": "Файл больше {mb} МБ — модель такой не примет.",
        "he": "הקובץ גדול מ-{mb} מ\"ב — המודל לא יקבל אותו.",
    },
    "add.err.need_input": {
        "en": "Give a link, a screenshot, or at least a name.",
        "ru": "Дайте ссылку, скриншот или хотя бы название.",
        "he": "תנו קישור, צילום מסך או לפחות שם.",
    },
    "add.err.parse_failed": {
        "en": "Couldn't parse it: {err}",
        "ru": "Не получилось разобрать: {err}",
        "he": "לא הצלחנו לפענח: {err}",
    },
    "add.err.failed": {
        "en": "Couldn't: {err}",
        "ru": "Не получилось: {err}",
        "he": "לא הצליח: {err}",
    },
    "add.err.no_gitea_token": {
        "en": "No GITEA_TOKEN — nothing to write with.",
        "ru": "Нет GITEA_TOKEN — писать нечем.",
        "he": "אין GITEA_TOKEN — אין במה לכתוב.",
    },
    # --- settings: token status and 2FA ---------------------------------------
    "settings.token_unreadable": {
        "en": "unreadable — enter it again",
        "ru": "нечитаем — впишите заново",
        "he": "לא קריא — הזינו מחדש",
    },
    "settings.2fa.err.bad_code_clock": {
        "en": "The code didn't match. Check that your phone's clock is accurate.",
        "ru": "Код не подошёл. Проверьте, что часы на телефоне точны.",
        "he": "הקוד לא התאים. ודאו שהשעון בטלפון מדויק.",
    },
    "settings.2fa.err.bad_code_no_regen": {
        "en": "The code didn't match — codes weren't regenerated.",
        "ru": "Код не подошёл — коды не перевыпущены.",
        "he": "הקוד לא התאים — הקודים לא הונפקו מחדש.",
    },
    "settings.2fa.err.bad_password": {
        "en": "Wrong password — 2FA wasn't turned off.",
        "ru": "Пароль неверный — проверка не выключена.",
        "he": "סיסמה שגויה — האימות לא כובה.",
    },
    # --- admin panel: shared-key labels ---------------------------------------
    "admin.key.gitea_url": {"en": "Gitea address", "ru": "Адрес Gitea", "he": "כתובת Gitea"},
    "admin.key.gitea_token": {"en": "Gitea token", "ru": "Токен Gitea", "he": "אסימון Gitea"},
    "admin.key.github_token": {"en": "GitHub token", "ru": "Токен GitHub", "he": "אסימון GitHub"},
    "admin.key.github_user": {
        "en": "GitHub account", "ru": "Аккаунт GitHub", "he": "חשבון GitHub",
    },
    "admin.key.google_key": {"en": "Google AI key", "ru": "Ключ Google AI", "he": "מפתח Google AI"},
    "admin.key.llm_model": {
        "en": "Description model",
        "ru": "Модель описаний",
        "he": "מודל התיאורים",
    },
    "admin.key.embedding_model": {
        "en": "Search model",
        "ru": "Модель поиска",
        "he": "מודל החיפוש",
    },
    # --- category folders: shared and private ---------------------------------
    "err.category_not_found": {
        "en": "Folder not found.",
        "ru": "папка не найдена",
        "he": "התיקייה לא נמצאה.",
    },
    "err.categorize_forbidden": {
        "en": "You can't file this item into that folder.",
        "ru": "нельзя положить эту карточку в эту папку",
        "he": "אי אפשר לתייק פריט זה בתיקייה הזו.",
    },
    "side.my_folders": {"en": "My folders", "ru": "Мои папки", "he": "התיקיות שלי"},
    "side.folders_head": {"en": "Folders", "ru": "Папки", "he": "תיקיות"},
    "side.shared_tag": {"en": "shared", "ru": "общая", "he": "משותפת"},
    "side.collapse_aria": {
        "en": "Collapse sidebar", "ru": "Свернуть панель", "he": "כיווץ הסרגל",
    },
    "side.expand_aria": {
        "en": "Show sidebar", "ru": "Показать панель", "he": "הצגת הסרגל",
    },
    "settings.edit": {"en": "Edit", "ru": "Изменить", "he": "עריכה"},
    "settings.source_delete_confirm": {
        "en": "Delete repository “{name}”?",
        "ru": "Удалить репозиторий «{name}»?",
        "he": "למחוק את המאגר „{name}“?",
    },
    "settings.shared_categories_title": {
        "en": "Shared folders",
        "ru": "Общие папки",
        "he": "תיקיות משותפות",
    },
    "settings.shared_categories_tip": {
        "en": "Folders in the shared catalogue — everyone sees them. Only you, the owner, arrange them.",
        "ru": "Папки общего каталога — их видят все. Раскладываете только вы, владелец.",
        "he": "תיקיות בקטלוג המשותף — כולם רואים אותן. רק אתם, הבעלים, מסדרים אותן.",
    },
    "settings.my_categories_title": {
        "en": "My folders",
        "ru": "Мои папки",
        "he": "התיקיות שלי",
    },
    "settings.my_categories_tip": {
        "en": "Your private folders. Only you see them, and you can file any item you can see into them.",
        "ru": "Ваши личные папки. Видите только вы; в них можно класть любую карточку, которую вы видите.",
        "he": "התיקיות הפרטיות שלכם. רק אתם רואים אותן, ואפשר לתייק בהן כל פריט שאתם רואים.",
    },
    "settings.new_shared_folder_placeholder": {
        "en": "New shared folder",
        "ru": "Новая общая папка",
        "he": "תיקייה משותפת חדשה",
    },
    "artifact.folders_title": {"en": "Folders", "ru": "Папки", "he": "תיקיות"},
    "artifact.no_folders": {
        "en": "Not in any folder yet.",
        "ru": "Пока ни в одной папке.",
        "he": "עדיין לא בשום תיקייה.",
    },
    "artifact.add_to_folder": {
        "en": "Add to folder…",
        "ru": "В папку…",
        "he": "לתיקייה…",
    },
    "artifact.remove_from_folder": {
        "en": "Remove from folder",
        "ru": "Убрать из папки",
        "he": "הסרה מהתיקייה",
    },
    "artifact.add": {"en": "Add", "ru": "Добавить", "he": "הוספה"},
    "card.file_failed": {
        "en": "Couldn't add to the folder.",
        "ru": "Не удалось положить в папку.",
        "he": "לא ניתן להוסיף לתיקייה.",
    },
    # --- managing users (registration, invitations, deletion) -----------------
    "auth.err.email_taken": {
        "en": "That email is already registered.",
        "ru": "Эта почта уже занята.",
        "he": "הדוא\"ל הזה כבר רשום.",
    },
    "err.cant_delete_self": {
        "en": "You can't delete yourself.",
        "ru": "нельзя удалить самого себя",
        "he": "אי אפשר למחוק את עצמך.",
    },
    "auth.register.title": {"en": "Create account", "ru": "Регистрация", "he": "יצירת חשבון"},
    "auth.register.lede": {
        "en": "Set up your account to use this catalogue.",
        "ru": "Заведите аккаунт, чтобы пользоваться каталогом.",
        "he": "צרו חשבון כדי להשתמש בקטלוג.",
    },
    "auth.register.submit": {
        "en": "Create account",
        "ru": "Завести аккаунт",
        "he": "יצירת חשבון",
    },
    "auth.register.link": {
        "en": "No account? Register",
        "ru": "Нет аккаунта? Регистрация",
        "he": "אין חשבון? הרשמה",
    },
    "auth.register_closed.title": {
        "en": "Registration is closed",
        "ru": "Регистрация закрыта",
        "he": "ההרשמה סגורה",
    },
    "auth.register_closed.lede": {
        "en": "The owner hasn't opened sign-ups. Ask them for an invite.",
        "ru": "Хозяин не открыл свободную регистрацию. Попросите у него приглашение.",
        "he": "הבעלים לא פתח הרשמה חופשית. בקשו הזמנה.",
    },
    "auth.join.title": {"en": "Accept invitation", "ru": "Принять приглашение", "he": "קבלת הזמנה"},
    "auth.join.lede": {
        "en": "Set your name and password to join.",
        "ru": "Задайте имя и пароль, чтобы войти.",
        "he": "הגדירו שם וסיסמה כדי להצטרף.",
    },
    "auth.join.submit": {"en": "Join", "ru": "Присоединиться", "he": "הצטרפות"},
    "auth.join_bad.title": {
        "en": "Invitation not valid",
        "ru": "Приглашение недействительно",
        "he": "ההזמנה אינה תקפה",
    },
    "auth.join_bad.lede": {
        "en": "This invitation link is wrong, expired, or already used.",
        "ru": "Эта ссылка-приглашение неверна, просрочена или уже использована.",
        "he": "קישור ההזמנה שגוי, פג תוקף או כבר נוצל.",
    },
    "admin.access.h": {
        "en": "Access & invitations",
        "ru": "Доступ и приглашения",
        "he": "גישה והזמנות",
    },
    "admin.access.legend": {
        "en": "Let people in: open registration, or invite them by link.",
        "ru": "Как пускать людей: открыть регистрацию или позвать ссылкой.",
        "he": "איך להכניס אנשים: לפתוח הרשמה או להזמין בקישור.",
    },
    "admin.reg.h": {"en": "Registration", "ru": "Регистрация", "he": "הרשמה"},
    "admin.reg.tip": {
        "en": "Open sign-up lets anyone create an account.",
        "ru": "Свободная регистрация — любой заведёт аккаунт сам.",
        "he": "הרשמה פתוחה מאפשרת לכל אחד ליצור חשבון.",
    },
    "admin.invite.h": {"en": "Invitations", "ru": "Приглашения", "he": "הזמנות"},
    "admin.invite.tip": {
        "en": "Invite by link; email it too if mail is set up.",
        "ru": "Позвать ссылкой; письмом — если настроена почта.",
        "he": "הזמנה בקישור; גם בדוא\"ל אם הדואר מוגדר.",
    },
    "admin.reg.label": {
        "en": "Anyone can register",
        "ru": "Любой может зарегистрироваться",
        "he": "כל אחד יכול להירשם",
    },
    "admin.reg.save": {"en": "Save", "ru": "Сохранить", "he": "שמירה"},
    "admin.invite.email": {
        "en": "Invite by email (optional)",
        "ru": "Пригласить по почте (необязательно)",
        "he": "הזמנה בדוא\"ל (רשות)",
    },
    "admin.invite.email_ph": {
        "en": "name@example.com",
        "ru": "name@example.com",
        "he": "name@example.com",
    },
    "admin.invite.create": {
        "en": "Create invite link",
        "ru": "Создать ссылку-приглашение",
        "he": "יצירת קישור הזמנה",
    },
    "admin.invite.link_ready": {
        "en": "Invite link — copy and share it:",
        "ru": "Ссылка-приглашение — скопируйте и передайте:",
        "he": "קישור הזמנה — העתיקו ושתפו:",
    },
    "admin.invite.sent": {
        "en": "Invite emailed to {to}.",
        "ru": "Приглашение отправлено на {to}.",
        "he": "ההזמנה נשלחה אל {to}.",
    },
    "admin.reset.link_ready": {
        "en": "Password-reset link — copy and share it:",
        "ru": "Ссылка для смены пароля — скопируйте и передайте:",
        "he": "קישור לאיפוס סיסמה — העתיקו ושתפו:",
    },
    "admin.reset.sent": {
        "en": "Reset link emailed to {to}.",
        "ru": "Ссылка для сброса отправлена на {to}.",
        "he": "קישור האיפוס נשלח אל {to}.",
    },
    "admin.reset.no_secret": {
        "en": "No SECRET_KEY — can't sign the link.",
        "ru": "Нет SECRET_KEY — ссылку не подписать.",
        "he": "אין SECRET_KEY — אי אפשר לחתום על הקישור.",
    },
    "admin.users.reset_pw": {"en": "Reset password", "ru": "Сбросить пароль", "he": "איפוס סיסמה"},
    "admin.users.delete": {"en": "Delete", "ru": "Удалить", "he": "מחיקה"},
    "admin.users.delete_confirm": {
        "en": "Delete {name}? Their private items and folders are removed; shared items pass to you.",
        "ru": "Удалить {name}? Личные карточки и папки удалятся; общие перейдут к вам.",
        "he": "למחוק את {name}? הפריטים והתיקיות הפרטיים יימחקו; המשותפים יעברו אליכם.",
    },
    # --- invitation email -----------------------------------------------------
    "email.invite.subject": {
        "en": "You're invited to VivAtlas",
        "ru": "Приглашение в VivAtlas",
        "he": "הוזמנת ל-VivAtlas",
    },
    "email.invite.preheader": {
        "en": "Set up your account — the link is valid for {days} days.",
        "ru": "Заведите аккаунт — ссылка действует {days} дней.",
        "he": "הקימו חשבון — הקישור תקף {days} ימים.",
    },
    "email.invite.h1": {
        "en": "Join VivAtlas",
        "ru": "Присоединяйтесь к VivAtlas",
        "he": "הצטרפו ל-VivAtlas",
    },
    "email.invite.intro": {
        "en": "You've been invited. Set your name and password to create your account. The link is valid for {days} days.",
        "ru": "Вас пригласили. Задайте имя и пароль, чтобы завести аккаунт. Ссылка действует {days} дней.",
        "he": "הוזמנתם. הגדירו שם וסיסמה כדי ליצור חשבון. הקישור תקף {days} ימים.",
    },
    "email.invite.button": {
        "en": "Accept invitation",
        "ru": "Принять приглашение",
        "he": "קבלת ההזמנה",
    },
    "email.invite.manual": {
        "en": "Or paste this link into your browser:",
        "ru": "Или вставьте эту ссылку в браузер:",
        "he": "או הדביקו את הקישור בדפדפן:",
    },
    "email.invite.ignore": {
        "en": "Didn't expect this? You can ignore this email.",
        "ru": "Не ждали? Просто не открывайте ссылку.",
        "he": "לא ציפיתם לזה? אפשר להתעלם מהמייל.",
    },
    "email.invite.footer_note": {
        "en": "This invitation was sent from VivAtlas.",
        "ru": "Это приглашение отправлено из VivAtlas.",
        "he": "הזמנה זו נשלחה מ-VivAtlas.",
    },
    "email.invite.txt_heading": {
        "en": "Join VivAtlas",
        "ru": "Присоединяйтесь к VivAtlas",
        "he": "הצטרפו ל-VivAtlas",
    },
    "email.invite.txt_intro": {
        "en": "You've been invited. Open the link to set your name and password (valid {days} days):",
        "ru": "Вас пригласили. Откройте ссылку, чтобы задать имя и пароль (действует {days} дней):",
        "he": "הוזמנתם. פתחו את הקישור כדי להגדיר שם וסיסמה (תקף {days} ימים):",
    },
    "email.invite.txt_ignore": {
        "en": "Didn't expect this? Ignore this email.",
        "ru": "Не ждали? Не открывайте ссылку.",
        "he": "לא ציפיתם? התעלמו מהמייל.",
    },
    "email.invite.txt_footer_note": {
        "en": "Sent from VivAtlas.",
        "ru": "Отправлено из VivAtlas.",
        "he": "נשלח מ-VivAtlas.",
    },
    # --- settings sections (tabs) ---------------------------------------------
    "settings.tab_account": {"en": "Account", "ru": "Аккаунт", "he": "חשבון"},
    "settings.tab_appearance": {"en": "Appearance", "ru": "Вид", "he": "מראה"},
    "settings.tab_folders": {"en": "Folders", "ru": "Папки", "he": "תיקיות"},
    "settings.tab_repos": {"en": "Repositories", "ru": "Репозитории", "he": "מאגרים"},
    "settings.tab_security": {"en": "Security", "ru": "Безопасность", "he": "אבטחה"},
    "settings.tab_delete": {"en": "Delete account", "ru": "Удаление", "he": "מחיקת חשבון"},
    "settings.folder_exists": {
        "en": "A folder with that name already exists.",
        "ru": "Папка с таким именем уже есть.",
        "he": "כבר קיימת תיקייה בשם הזה.",
    },
    # --- account --------------------------------------------------------------
    "account.photo_title": {"en": "Profile photo", "ru": "Фото профиля", "he": "תמונת פרופיל"},
    "account.photo_hint": {
        "en": "PNG, JPEG, GIF, BMP or SVG — converted to WebP, cropped square.",
        "ru": "PNG, JPEG, GIF, BMP или SVG — переведём в WebP, обрежем в квадрат.",
        "he": "PNG, JPEG, GIF, BMP או SVG — יומר ל-WebP וייחתך לריבוע.",
    },
    "account.photo_upload": {"en": "Upload photo", "ru": "Загрузить фото", "he": "העלאת תמונה"},
    "account.photo_remove": {"en": "Remove photo", "ru": "Убрать фото", "he": "הסרת תמונה"},
    "account.photo_saved": {"en": "Photo updated.", "ru": "Фото обновлено.", "he": "התמונה עודכנה."},
    "account.photo_removed": {"en": "Photo removed.", "ru": "Фото убрано.", "he": "התמונה הוסרה."},
    "account.avatar_pick": {
        "en": "Or pick an avatar",
        "ru": "Или выберите аватар",
        "he": "או בחרו אווטאר",
    },
    "account.avatar_choose": {"en": "Choose this avatar", "ru": "Выбрать этот аватар", "he": "בחירת אווטאר זה"},
    "account.avatar_saved": {"en": "Avatar updated.", "ru": "Аватар обновлён.", "he": "האווטאר עודכן."},
    "account.avatar_bad": {"en": "Unknown avatar.", "ru": "Неизвестный аватар.", "he": "אווטאר לא ידוע."},
    "account.email_title": {"en": "Email", "ru": "Почта", "he": "דוא״ל"},
    "account.email_hint": {
        "en": "Changing your email needs your current password.",
        "ru": "Смена почты требует текущий пароль.",
        "he": "שינוי הדוא״ל דורש את הסיסמה הנוכחית.",
    },
    "account.new_email": {"en": "New email", "ru": "Новая почта", "he": "דוא״ל חדש"},
    "account.confirm_email": {
        "en": "Confirm new email", "ru": "Повтор новой почты", "he": "אישור הדוא״ל החדש",
    },
    "account.change_email_btn": {"en": "Change email", "ru": "Сменить почту", "he": "שינוי דוא״ל"},
    "account.email_changed": {
        "en": "Email changed.", "ru": "Почта изменена.", "he": "הדוא״ל שונה.",
    },
    "account.password_title": {"en": "Password", "ru": "Пароль", "he": "סיסמה"},
    "account.current_password": {
        "en": "Current password", "ru": "Текущий пароль", "he": "סיסמה נוכחית",
    },
    "account.new_password": {"en": "New password", "ru": "Новый пароль", "he": "סיסמה חדשה"},
    "account.confirm_password": {
        "en": "Confirm new password", "ru": "Повтор нового пароля", "he": "אישור הסיסמה החדשה",
    },
    "account.change_password_btn": {
        "en": "Change password", "ru": "Сменить пароль", "he": "שינוי סיסמה",
    },
    "account.pw_changed": {
        "en": "Password changed.", "ru": "Пароль изменён.", "he": "הסיסמה שונתה.",
    },
    "account.danger_title": {
        "en": "Delete account", "ru": "Удаление аккаунта", "he": "מחיקת חשבון",
    },
    "account.danger_hint": {
        "en": "Permanent. Your private items and folders go with you; shared items pass to the owner.",
        "ru": "Навсегда. Личные карточки и папки уходят с вами; общие переходят владельцу.",
        "he": "לצמיתות. הפריטים והתיקיות הפרטיים נמחקים; פריטים משותפים עוברים לבעלים.",
    },
    "account.delete_btn": {
        "en": "Delete my account", "ru": "Удалить мой аккаунт", "he": "מחיקת החשבון שלי",
    },
    "account.delete_confirm": {
        "en": "Delete your account permanently? This cannot be undone.",
        "ru": "Удалить аккаунт навсегда? Отменить будет нельзя.",
        "he": "למחוק את החשבון לצמיתות? לא ניתן לבטל.",
    },
    "account.err.bad_current": {
        "en": "Current password is wrong.",
        "ru": "Текущий пароль неверен.",
        "he": "הסיסמה הנוכחית שגויה.",
    },
    "account.err.pw_mismatch": {
        "en": "New passwords don't match.",
        "ru": "Новые пароли не совпадают.",
        "he": "הסיסמאות החדשות אינן תואמות.",
    },
    "account.err.email_bad": {
        "en": "Enter a valid email address.",
        "ru": "Введите правильный адрес почты.",
        "he": "יש להזין כתובת דוא״ל תקינה.",
    },
    "account.err.email_taken": {
        "en": "That email is already in use.",
        "ru": "Эта почта уже занята.",
        "he": "הדוא״ל הזה כבר בשימוש.",
    },
    "account.err.email_mismatch": {
        "en": "The two emails don't match.",
        "ru": "Адреса почты не совпадают.",
        "he": "כתובות הדוא״ל אינן תואמות.",
    },
    "account.err.last_owner": {
        "en": "You're the last owner — you can't delete yourself.",
        "ru": "Вы последний владелец — себя удалить нельзя.",
        "he": "אתם הבעלים האחרון — אי אפשר למחוק את עצמכם.",
    },
    # --- avatars (conversion errors) ------------------------------------------
    "avatar.err.empty": {"en": "No file was sent.", "ru": "Файл не выбран.", "he": "לא נשלח קובץ."},
    "avatar.err.too_big": {
        "en": "Image is too large (8 MB max).",
        "ru": "Картинка слишком большая (до 8 МБ).",
        "he": "התמונה גדולה מדי (עד 8 מ״ב).",
    },
    "avatar.err.unreadable": {
        "en": "Couldn't read that image.",
        "ru": "Не удалось прочитать картинку.",
        "he": "לא ניתן לקרוא את התמונה.",
    },
    "avatar.err.svg_unsupported": {
        "en": "SVG needs the browser engine, unavailable here — use PNG or JPEG.",
        "ru": "Для SVG нужен движок браузера, здесь его нет — возьмите PNG или JPEG.",
        "he": "SVG דורש מנוע דפדפן שאינו זמין כאן — השתמשו ב-PNG או JPEG.",
    },
    "avatar.err.svg_failed": {
        "en": "Couldn't render that SVG.",
        "ru": "Не удалось отрисовать SVG.",
        "he": "לא ניתן לעבד את ה-SVG.",
    },
    # --- admin: configuration -------------------------------------------------
    "admin.config.title": {
        "en": "AI", "ru": "ИИ", "he": "AI",
    },
    "admin.config.tip": {
        "en": "Google AI key and the models used for descriptions and search. Overrides .env, stored encrypted, applies without a restart. Leave a secret blank to keep it.",
        "ru": "Ключ Google AI и модели для описаний и поиска. Поверх .env, хранятся шифром, применяются без перезапуска. Пустой секрет — оставить прежний.",
        "he": "מפתח Google AI והמודלים לתיאורים ולחיפוש. גובר על .env, נשמר מוצפן, חל ללא הפעלה מחדש. השאירו סוד ריק כדי לשמור אותו.",
    },
    "admin.sources.title": {"en": "Sources", "ru": "Источники", "he": "מקורות"},
    "admin.sources.tip": {
        "en": "Where the catalog reads public repositories from — Gitea (whole instance) and one GitHub account. Overrides .env, stored encrypted, applies without a restart. Leave a token blank to keep it. Save, then Scan.",
        "ru": "Откуда каталог читает открытые репозитории — Gitea (весь сервер) и один аккаунт GitHub. Поверх .env, хранятся шифром, применяются без перезапуска. Пустой токен — оставить прежний. Сохраните, затем «Сканировать».",
        "he": "מהיכן הקטלוג קורא מאגרים ציבוריים — Gitea (כל השרת) וחשבון GitHub אחד. גובר על .env, נשמר מוצפן, חל ללא הפעלה מחדש. השאירו אסימון ריק כדי לשמור אותו. שמרו, ואז סרקו.",
    },
    "admin.sources.scan": {"en": "Scan now", "ru": "Сканировать", "he": "סריקה"},
    "admin.sources.scan_hint": {
        "en": "Pulls public repositories into the shared catalogue (visible to everyone). Save your changes first; progress shows on the home page.",
        "ru": "Забирает открытые репозитории в общий каталог (видят все). Сначала сохраните изменения; ход виден на главной.",
        "he": "מושך מאגרים ציבוריים לקטלוג המשותף (גלוי לכולם). שמרו קודם את השינויים; ההתקדמות מוצגת בעמוד הבית.",
    },
    "admin.sources.scan_none": {
        "en": "Set a Gitea address or a GitHub account first, then Save.",
        "ru": "Сначала укажите адрес Gitea или аккаунт GitHub и сохраните.",
        "he": "הזינו קודם כתובת Gitea או חשבון GitHub, ואז שמרו.",
    },
    "admin.config.saved": {
        "en": "Configuration saved.", "ru": "Настройки сохранены.", "he": "ההגדרות נשמרו.",
    },
    "admin.config.save_btn": {"en": "Save", "ru": "Сохранить", "he": "שמירה"},
    "admin.tab_users": {"en": "Users", "ru": "Люди", "he": "משתמשים"},
    "admin.tab_access": {"en": "Access", "ru": "Доступ", "he": "גישה"},
    "admin.tab_sources": {"en": "Sources", "ru": "Источники", "he": "מקורות"},
    "admin.tab_folders": {"en": "Folders", "ru": "Папки", "he": "תיקיות"},
    "admin.tab_mail": {"en": "Mail", "ru": "Почта", "he": "דוא״ל"},
    "admin.tab_integrations": {"en": "Integrations", "ru": "Интеграции", "he": "אינטגרציות"},
    "admin.tab_ai": {"en": "AI", "ru": "ИИ", "he": "AI"},
}

# Bulk translations for the remaining templates live in a separate generated
# file (one agent per template). We merge them in if it exists.
try:
    from vivatlas.translations_bulk import BULK

    CATALOG.update(BULK)
except ImportError:
    pass

