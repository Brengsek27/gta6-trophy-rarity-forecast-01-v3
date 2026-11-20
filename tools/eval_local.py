"""Local, offline comparison with the coordinator's recovered practice page."""

import argparse
import copy
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KIT = Path('/home/hatch/workspace/makefun/gta6-trophy-rarity-forecast-01-kit/gta6-trophy-rarity-forecast-01-kit')
LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
COUNTS = (
    4, 638, 8, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 4, 4, 5, 4, 4, 5,
    13, 6, 5, 5, 5, 4, 4, 5, 4, 5, 4, 4, 4, 6, 5, 8, 5, 6, 10,
    6, 5, 6, 5, 5, 6, 5, 5, 5, 5, 5, 5, 5, 5, 6, 5, 5,
)
INCREMENTS = {
    1: 1, 3: 2, 4: 2, 8: 3, 9: 1, 12: 5, 13: 1, 14: 1,
    16: 1, 17: 1, 18: 1, 19: 1, 20: 28, 21: 3, 31: 1,
    35: 8, 36: 2, 37: 3, 38: 24, 39: 2, 44: 1, 45: 1,
    46: 2, 47: 1, 50: 1, 51: 1, 52: 2, 53: 1, 55: 2,
}


def logit(rate):
    rate = max(0.01, min(99.99, rate))
    return math.log(rate / (100 - rate))


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result, f'duplicate JSON key: {key}'
        result[key] = value
    return result


def validate(payload, case):
    assert len(payload) <= 65536, 'oversized output'
    result = json.loads(payload, object_pairs_hook=unique_object)
    assert type(result) is dict and set(result) == {'trophies'}
    rows = result['trophies']
    assert type(rows) is list and len(rows) == len(case['trophies'])
    for row, trophy in zip(rows, case['trophies']):
        assert type(row) is dict and set(row) == {'id', 'q'}
        assert type(row['id']) is int and row['id'] == trophy['id']
        values = row['q']
        assert type(values) is list and len(values) == 5
        assert all(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100 for value in values)
        assert values == sorted(values), 'quantiles out of order'
    return rows


def run(program, case):
    durations = []
    payloads = []
    with tempfile.TemporaryDirectory(prefix='gta6-eval-') as directory:
        base = Path(directory)
        source = base / 'input'
        source.mkdir()
        (source / (case['id'] + '.in')).write_text(json.dumps(case), encoding='utf-8')
        for repeat in range(2):
            destination = base / f'output-{repeat}'
            environment = dict(os.environ, MAKEFUN_INPUT_DIR=str(source), MAKEFUN_OUTPUT_DIR=str(destination), PYTHONHASHSEED='0')
            started = time.perf_counter()
            process = subprocess.run([sys.executable, '-P', '-s', str(program)], env=environment, capture_output=True, timeout=8)
            duration = time.perf_counter() - started
            assert process.returncode == 0, process.stderr.decode(errors='replace')
            assert duration < 8
            paths = sorted(destination.iterdir())
            assert [path.name for path in paths] == [case['id'] + '.out']
            payload = paths[0].read_bytes()
            validate(payload, case)
            payloads.append(payload)
            durations.append(duration * 1000)
    assert payloads[0] == payloads[1], 'nondeterministic bytes'
    return validate(payloads[0], case), durations, len(payloads[0])


def score(rows, answers):
    losses = []
    errors = []
    coverage = []
    assert len(rows) == len(answers)
    for row, answer in zip(rows, answers):
        values = row['q']
        for level, prediction in zip(LEVELS, values):
            difference = logit(answer) - logit(prediction)
            losses.append(2 * max(level * difference, (level - 1) * difference))
        errors.append(abs(answer - values[2]))
        coverage.append(values[0] <= answer <= values[-1])
    return sum(losses) / len(losses), sum(errors) / len(errors), sum(coverage) / len(coverage)


def recovered_answers():
    assert len(COUNTS) == 55 and sum(COUNTS) == 943
    assert sum(INCREMENTS.values()) == 103
    refreshed = [3868 if trophy_id == 2 else count + INCREMENTS.get(trophy_id, 0)
                 for trophy_id, count in enumerate(COUNTS, 1)]
    assert sum(refreshed) == 4276
    return [round(100 * count / 3868, 2) for count in refreshed]


def table(title, headings, rows):
    print('\n' + title)
    formatted = [[str(item) for item in headings]] + [[str(item) for item in row] for row in rows]
    widths = [max(len(row[index]) for row in formatted) for index in range(len(headings))]
    for row in formatted:
        print('  '.join(value.ljust(width) for value, width in zip(row, widths)))


def final_like(template):
    case = copy.deepcopy(template)
    case['id'] = 'synthetic-final'
    checkpoint = datetime(2026, 11, 21, 11, 30, tzinfo=timezone.utc)
    case['at'] = checkpoint.isoformat()
    case['horizon_minutes'] = 6510
    case['target'] = (checkpoint + timedelta(minutes=6510)).isoformat()
    start = checkpoint - timedelta(days=2, minutes=10)
    case['history'] = []
    for step in range(13):
        read_at = start + timedelta(hours=4 * step)
        rates = [100 if trophy['id'] == 2 else round((0.5 + index * 0.03) * (1 + step / 6), 4)
                 for index, trophy in enumerate(case['trophies'])]
        case['history'].append({'slot': read_at.isoformat(), 'at': read_at.isoformat(),
                                'players_tracked': 1000 + step * 350,
                                'trophies_earned': 0, 'rates': rates})
    return case


def check_final(program, template):
    case = final_like(template)
    rows, durations, size = run(program, case)
    rising = [(index, row) for index, row in enumerate(rows) if row['id'] != 2]
    above = sum(row['q'][2] > case['history'][-1]['rates'][index] for index, row in rising)
    assert above == len(rising), 'a rising trophy failed to extrapolate upward'
    spreads = [(logit(row['q'][3]) - logit(row['q'][1])) / (2 * 0.674) for _, row in rising]
    mean_spread = sum(spreads) / len(spreads)
    days = 6510 / 1440
    expected = math.sqrt(0.25 * days + 0.12 ** 2 + (0.9142 * math.exp(-days / 4)) ** 2)
    assert 1.0 < mean_spread < 1.3
    assert abs(mean_spread - expected) < 0.01
    check_live_reference(case, rows)
    universal = next(row for row in rows if row['id'] == 2)
    assert universal['q'][2] == 99.99
    table('Synthetic final-like case (two days of rising reads; 6510-minute horizon)',
          ['Rising medians > last', 'Mean spread', 'Formula spread', 'Deterministic', 'Max ms', 'Bytes'],
          [[f'{above}/{len(rising)}', f'{mean_spread:.6f}', f'{expected:.6f}', 'yes', f'{max(durations):.2f}', size]])
    return case


def check_live_reference(case, rows):
    release = datetime.fromisoformat(case['game']['release_date']).replace(tzinfo=timezone.utc)
    target = datetime.fromisoformat(case['target'].replace('Z', '+00:00'))
    target_x = math.log1p((target - release).total_seconds() / 3600)
    coordinates = [math.log1p((datetime.fromisoformat(read['at'].replace('Z', '+00:00')) - release).total_seconds() / 3600)
                   for read in case['history']]
    weights = [math.exp(2 * (coordinate - coordinates[-1])) for coordinate in coordinates]

    def reference_fit(indices, values):
        total = sum(weights[index] for index in indices)
        center = sum(weights[index] * coordinates[index] for index in indices) / total
        average = sum(weights[index] * value for index, value in zip(indices, values)) / total
        numerator = sum(weights[index] * (coordinates[index] - center) * (value - average)
                        for index, value in zip(indices, values))
        denominator = sum(weights[index] * (coordinates[index] - center) ** 2 for index in indices)
        slope = numerator / denominator if denominator > 1e-15 else 0
        residual = math.sqrt(sum(weights[index] * (value - average - slope * (coordinates[index] - center)) ** 2
                                 for index, value in zip(indices, values)) / total)
        return average + slope * (target_x - center), residual

    days = case['horizon_minutes'] / 1440
    for position, row in enumerate(rows):
        indices = [index for index, read in enumerate(case['history']) if read['rates'][position] is not None]
        if not indices or all(read['rates'][position] == 100 for read in case['history']):
            continue
        trend, residual = reference_fit(indices, [logit(case['history'][index]['rates'][position]) for index in indices])
        count, _ = reference_fit(indices, [math.log(max(1e-12, case['history'][index]['rates'][position] * case['history'][index]['players_tracked'] / 100)) for index in indices])
        players, _ = reference_fit(indices, [math.log(case['history'][index]['players_tracked']) for index in indices])
        median = (trend + logit(100 * math.exp(count - players))) / 2 - 1.25 * math.exp(-days / 2)
        spread = math.sqrt(0.25 * days + min(1, max(0.12, residual)) ** 2 + (0.9142 * math.exp(-days / 4)) ** 2)
        expected = [round(max(0.01, min(99.99, 100 / (1 + math.exp(-(median + normal * spread))))), 4)
                    for normal in (-1.645, -0.674, 0, 0.674, 1.645)]
        assert row['q'] == expected, f'live reference mismatch at trophy {row["id"]}'


def check_edges(program, template, live):
    missing = copy.deepcopy(live)
    missing['id'] = 'missing-rates'
    for read in missing['history']:
        read['rates'][0] = None
    missing['history'][0]['rates'][2] = None
    rows, _, _ = run(program, missing)
    assert rows[0]['q'][2] == 0.01
    check_live_reference(missing, rows)
    missing_frozen = copy.deepcopy(template)
    missing_frozen['id'] = 'frozen-missing'
    for read in missing_frozen['history']:
        read['rates'][0] = None
    rows, _, _ = run(program, missing_frozen)
    assert rows[0]['q'][2] == 0.01
    generic = copy.deepcopy(template)
    generic['id'] = 'generic-frozen'
    generic['trophies'][0]['id'] = 99
    for read in generic['history']:
        read['players_tracked'] = 1276
    rows, _, _ = run(program, generic)
    assert rows[0]['q'] == [0.17] * 5
    assert rows[1]['q'] == [100] * 5
    print('\nEdge cases: PASS (all-null, partially missing, frozen null, scaled unknown ID, universal trophy).')
    print('Live formula reference: PASS for every eligible quantile, including partially missing histories.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kit', type=Path, default=DEFAULT_KIT)
    args = parser.parse_args()
    print(f'Interpreter: {sys.version.split()[0]}; no network or makefun commands used.')
    answers = recovered_answers()
    print('Recovered page: 3868 players, 4276 trophies earned; independently constructed answers.')
    programs = {'round3': ROOT / 'main.py', 'starter': args.kit / 'starter' / 'main.py'}
    forecasts = {}
    scored = []
    validation = []
    cases = [json.loads((args.kit / 'input' / f'{name}.in').read_text()) for name in ('p00', 'p01')]
    for case in cases:
        assert [round(rate * 638 / 100) for rate in case['history'][-1]['rates']] == list(COUNTS)
        for label, program in programs.items():
            rows, durations, size = run(program, case)
            forecasts[label, case['id']] = rows
            metrics = score(rows, answers)
            scored.append([label, case['id'], *[f'{value:.6f}' for value in metrics]])
            validation.append([label, case['id'], 'PASS', 'yes', size, *[f'{duration:.2f}' for duration in durations]])
    for label in programs:
        combined = [row for case in cases for row in forecasts[label, case['id']]]
        metrics = score(combined, answers * len(cases))
        scored.append([label, 'combined', *[f'{value:.6f}' for value in metrics]])
    table('Strict validation and two independent executions',
          ['Program', 'Case', 'Valid', 'Byte-identical', 'Bytes', 'Run 1 ms', 'Run 2 ms'], validation)
    table('Recovered practice answers', ['Program', 'Case', 'Loss', 'Typical miss (points)', '90% coverage'], scored)
    target = score(forecasts['round3', 'p00'], answers)
    target_pass = target[0] <= 0.001 and target[1] == 0 and target[2] == 1
    print('\nRecovered-answer target: ' + ('PASS' if target_pass else 'FAIL; constants have not been retuned.'))
    scenario_rows = []
    for phi in (0, 0.1, 0.25, 0.5):
        scenario = [100 if trophy_id == 2 else round(100 * count / 638 * (638 + 3230 * phi) / 3868, 2)
                    for trophy_id, count in enumerate(COUNTS, 1)]
        for label in programs:
            metrics = score(forecasts[label, 'p00'], scenario)
            scenario_rows.append([phi, label, *[f'{value:.6f}' for value in metrics]])
    table('Older synthetic refresh scenarios (same context for p00 and p01)',
          ['Phi', 'Program', 'Loss', 'Typical miss (points)', '90% coverage'], scenario_rows)
    print('\nRound 3 deliberately trades those scenarios away: it is a point bet on the recovered refresh.\n'
          'Poor phi 0.25 and phi 0.5 losses are expected and accepted.')
    live = check_final(programs['round3'], cases[0])
    check_edges(programs['round3'], cases[0], live)
    assert target_pass, 'recovered-answer target missed'


if __name__ == '__main__':
    main()
