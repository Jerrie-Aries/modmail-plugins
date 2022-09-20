from __future__ import annotations

import re
from typing import Callable, Generator, Iterable, List, Literal, Optional, Tuple, Union, overload


@overload
def finder(
    text: str,
    collection: Iterable[str],
    *,
    key: Optional[Callable[[str], str]] = ...,
    lazy: Literal[True] = ...,
) -> Generator[str, None, None]:
    ...


@overload
def finder(
    text: str,
    collection: Iterable[str],
    *,
    key: Optional[Callable[[str], str]] = ...,
    lazy: Literal[False],
) -> List[str]:
    ...


@overload
def finder(
    text: str,
    collection: Iterable[str],
    *,
    key: Optional[Callable[[str], str]] = ...,
    lazy: bool = ...,
) -> Union[Generator[str, None, None], List[str]]:
    ...


def finder(
    text: str,
    collection: Iterable[str],
    *,
    key: Optional[Callable[[str], str]] = ...,
    lazy: bool = True,
) -> Union[Generator[str, None, None], List[str]]:
    suggestions: List[Tuple[int, int, str]] = []
    text = str(text)
    pat = ".*?".join(map(re.escape, text))
    regex = re.compile(pat, flags=re.IGNORECASE)
    for item in collection:
        to_search = key(item) if key else item
        r = regex.search(to_search)
        if r:
            suggestions.append((len(r.group()), r.start(), item))

    def sort_key(tup: Tuple[int, int, str]) -> Tuple[int, int, str]:
        if key:
            return tup[0], tup[1], key(tup[2])
        return tup

    if lazy:
        return (z for _, _, z in sorted(suggestions, key=sort_key))
    else:
        return [z for _, _, z in sorted(suggestions, key=sort_key)]


def find(
    text: str, collection: Iterable[str], *, key: Optional[Callable[[str], str]] = None
) -> Optional[str]:
    try:
        return finder(text, collection, key=key, lazy=False)[0]
    except IndexError:
        return None
