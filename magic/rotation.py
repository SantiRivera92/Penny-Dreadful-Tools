import datetime
import glob
import os
from collections import Counter
from typing import Dict, List, Optional, Tuple

from magic import multiverse, oracle, seasons
from magic.models import Card
from shared import configuration, dtutil
from shared import redis_wrapper as redis
from shared import text
from shared.pd_exception import DoesNotExistException, InvalidDataException

TOTAL_RUNS = 168

def interesting(playability: Dict[str, float], c: Card, speculation: bool = True, new: bool = True) -> Optional[str]:
    if new and len({k: v for (k, v) in c['legalities'].items() if 'Penny Dreadful' in k}) == (0 if speculation else 1):
        return 'new'
    p = playability.get(c.name, 0)
    if p > 0.1:
        return 'heavily-played'
    if p > 0.01:
        return 'moderately-played'
    return None

def in_rotation() -> bool:
    if configuration.get_bool('always_show_rotation'):
        return True
    until_rotation = seasons.next_rotation() - dtutil.now()
    return until_rotation < datetime.timedelta(7)

def files() -> List[str]:
    return sorted(glob.glob(os.path.expanduser(os.path.join(configuration.get_str('legality_dir'), 'Run_*.txt'))))

def last_run_time() -> Optional[datetime.datetime]:
    try:
        return dtutil.ts2dt(int(os.path.getmtime(files()[-1])))
    except (IndexError, OSError):
        return None

def read_rotation_files() -> Tuple[int, int, List[Card]]:
    runs_str = redis.get_str('decksite:rotation:summary:runs')
    runs_percent_str = redis.get_str('decksite:rotation:summary:runs_percent')
    cards = redis.get_list('decksite:rotation:summary:cards')
    if runs_str is not None and runs_percent_str is not None and cards is not None:
        return int(runs_str), int(runs_percent_str), [Card(c, predetermined_values=True) for c in cards]
    return rotation_redis_store()

def rotation_redis_store() -> Tuple[int, int, List[Card]]:
    lines = []
    fs = files()
    if len(fs) == 0:
        if not os.path.isdir(os.path.expanduser(configuration.get_str('legality_dir'))):
            print('WARNING: Could not find legality_dir.')
        return (0, 0, [])
    with open(fs[-1], 'r') as f:
        latest_list = f.read().splitlines()
    for filename in fs:
        for line in get_file_contents(filename):
            line = text.sanitize(line)
            lines.append(line.strip())
    scores = Counter(lines).most_common()
    runs = scores[0][1]
    runs_percent = round(round(runs / TOTAL_RUNS, 2) * 100)
    cs = oracle.cards_by_name()
    cards = []
    card_names_by_status: Dict[str, List[str]] = {}
    for name, hits in scores:
        c = process_score(name, hits, cs, runs, latest_list)
        if c is not None:
            cards.append(c)
            classify_by_status(c, card_names_by_status)
    redis.store('decksite:rotation:summary:runs', runs, ex=604800)
    redis.store('decksite:rotation:summary:runs_percent', runs_percent, ex=604800)
    redis.store('decksite:rotation:summary:cards', cards, ex=604800)
    if 'Undecided' in card_names_by_status:
        redis.sadd('decksite:rotation:summary:undecided', *card_names_by_status['Undecided'], ex=604800)
    if 'Legal' in card_names_by_status:
        redis.sadd('decksite:rotation:summary:legal', *card_names_by_status['Legal'], ex=604800)
    if 'Not Legal' in card_names_by_status:
        redis.sadd('decksite:rotation:summary:notlegal', *card_names_by_status['Not Legal'], ex=604800)
    return (runs, runs_percent, cards)

def get_file_contents(file: str) -> List[str]:
    key = f'decksite:rotation:file:{file}'
    contents = redis.get_list(key)
    if contents is not None:
        return contents
    with open(file) as f:
        contents = f.readlines()
    redis.store(key, contents, ex=604800)
    return contents

def clear_redis(clear_files: bool = False) -> None:
    redis.clear(*redis.keys('decksite:rotation:summary:*'))
    if clear_files:
        redis.clear(*redis.keys('decksite:rotation:file:*'))

def process_score(name: str, hits: int, cs: Dict[str, Card], runs: int, latest_list: List[str]) -> Optional[Card]:
    remaining_runs = TOTAL_RUNS - runs
    hits_needed = max(round(TOTAL_RUNS / 2 - hits), 0)
    c = cs[name]
    if not multiverse.is_playable_layout(c.layout):
        return None
    percent = round(round(hits / runs, 2) * 100)
    if remaining_runs == 0:
        percent_needed = '0'
    else:
        percent_needed = str(round(round(hits_needed / remaining_runs, 2) * 100))
    if c is None:
        raise DoesNotExistException("Legality list contains unknown card '{name}'".format(name=name))
    if remaining_runs + hits < TOTAL_RUNS / 2:
        status = 'Not Legal'
    elif hits >= TOTAL_RUNS / 2:
        status = 'Legal'
    else:
        status = 'Undecided'
    hit_in_last_run = name in latest_list
    c.update({
        'hits': hits,
        'hits_needed': hits_needed,
        'percent': percent,
        'percent_needed': percent_needed,
        'status': status,
        'hit_in_last_run': hit_in_last_run,
    })
    return c

def classify_by_status(c: Card, card_names_by_status: Dict[str, List[str]]) -> None:
    if not c.status in card_names_by_status:
        card_names_by_status[c.status] = []
    card_names_by_status[c.status].append(c.name)

# Sort a list of cards with rotation information annotated by the specified field and sort order. Sorts in-place for speed.
def rotation_sort(cs: List[Card], sort_by: Optional[str], sort_order: Optional[str]) -> None:
    if not sort_by:
        sort_by = 'hitsNeeded'
        sort_order = 'ASC'
    else:
        sort_by = str(sort_by)
        sort_order = str(sort_order)
    rev = sort_order == 'DESC'
    cs.sort(key=lambda c: c.name)  # Let's have the cards in alphabetical order, everything else being equal.
    sort_funcs = {
        'hitInLastRun': lambda c: (c.hit_in_last_run, -hits_needed_score(c) if rev else hits_needed_score(c)),
        'hits': lambda c: c.hits,
        'name': lambda c: c.name,
        'hitsNeeded': hits_needed_score,
    }
    cs.sort(key=sort_funcs[sort_by], reverse=rev)

# As both primary and secondary sort we want to be able to sort cards by:
#     1. Can still be legal but are not yet confirmed, most hits first (to show the cards most likely to make it at the top).
#     2. Confirmed legal, least hits first (to show the cards most likely to have made it in recently at the top).
#     3. Confirmed not legal, most hits first (to show the cards that came closest to making it at the top).
def hits_needed_score(c: Card) -> int:
    if c.status == 'Undecided':
        return TOTAL_RUNS - c.hits
    if c.status == 'Legal':
        return TOTAL_RUNS + c.hits
    if c.status == 'Not Legal':
        return TOTAL_RUNS * 3 - c.hits
    raise InvalidDataException(f'Card status of `{c.status}` not recognized, did you pass a Card with rotation information?')
