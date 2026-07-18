"""Права и видимость папок-категорий: общие (админские) и личные (пер-user).

Одна точка правды, чтобы приватность нельзя было забыть на каком-то экране:

- ОБЩАЯ папка (``owner_user_id`` пуст) — часть общего каталога: видят все,
  ведёт (заводит/переименовывает/удаляет) только администратор.
- ЛИЧНАЯ папка (``owner_user_id`` задан) — личное дело одного человека: видит,
  ведёт и раскладывает в неё только он. Даже администратор её НЕ видит.

Членство карточки в папке живёт в ``ArtifactCategory`` (многие-ко-многим): одна
карточка может лежать и в общей папке, и в личных папках у разных людей — у
каждого своя строка. Чужое личное членство другим не показываем.
"""

from sqlalchemy import Select, select

from vivatlas.models import Artifact, Category


def visible_category_ids(user_id: int | None) -> Select:
    """id папок, которые вправе видеть этот человек: все общие + свои личные.
    Аноним — только общие."""
    cond = Category.owner_user_id.is_(None)
    if user_id is not None:
        cond = cond | (Category.owner_user_id == user_id)
    return select(Category.id).where(cond)


def can_view(cat: Category, user_id: int | None) -> bool:
    """Видит ли человек эту папку: общую — да; личную — только её владелец."""
    return cat.owner_user_id is None or (
        user_id is not None and cat.owner_user_id == user_id
    )


def can_manage(cat: Category, user_id: int | None, is_admin: bool) -> bool:
    """Кто вправе править/удалять/переставлять папку: общую — администратор;
    личную — её владелец. Чужую личную — никто (её и не видно)."""
    if user_id is None:
        return False
    if cat.owner_user_id is None:
        return is_admin
    return cat.owner_user_id == user_id


def can_file(art: Artifact, cat: Category, user_id: int | None, is_admin: bool) -> bool:
    """Можно ли положить карточку ``art`` в папку ``cat`` (или вынуть).

    - в ОБЩУЮ папку: раскладывает ТОЛЬКО администратор, и только общую (shared)
      карточку. Люди не «настраивают» общие папки — они делятся карточкой
      (делают её общей), и она появляется у всех в каталоге; как разложить общий
      каталог по папкам — решает администратор;
    - в СВОЮ ЛИЧНУЮ папку: любую карточку, которую человек вправе ВИДЕТЬ (личная
      папка — это личная полка поверх каталога, как избранное);
    - в чужую личную папку: никогда.

    Видимость самой ``art`` проверяется у вызывающего (до этого вызова)."""
    if user_id is None:
        return False
    if cat.owner_user_id is None:  # общая папка — только администратор
        return is_admin and art.shared
    return cat.owner_user_id == user_id  # своя личная
