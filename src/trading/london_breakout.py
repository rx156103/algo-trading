import argparse
import logging
from datetime import datetime
import pandas as pd

from src.common import api_request, transform
from src.finta.utils import trending_up, trending_down
from src.notifier import notify
from src.order_utils.order_api import placing_order, get_pending_orders, cancel_order, OrderType, get_trans
from src.position_calculator import pos_size

# Rules:
#   1. Find the high and low between 00:00 to 08:00 UTC
#   2. Place a buy stop order 2 pips above high with stop loss
#       Buy Stop:
#       At: (Maximum Price + 2 pips)
#       T/P: (Maximum Price + (Maximum Price - Minimum Price))
#       S/L: (Minimum Price - 2 pips)
#   3. Place a sell stop order 2 pips below low
#       Sell Stop:
#       At (Minimum Price - 2 pips)
#       T/P (Minimum Price - (Maximum Price - Minimum Price))
#       S/L (Maximum Price + 2 pips)
#   4. Inactive pending orders will expire next trading day at 08:00 AM (GMT).


ADJUSTMENT = 5 / 10000
LOOK_BACK_HOURS = 10
TREND_WINDOW = 60


def cancel_pending_orders():
    pending_orders = get_pending_orders()
    if pending_orders:
        for o in pending_orders:
            # We do not want to cancel TAKE_PROFIT and STOP_LOSS pending orders
            if o.get('type') == 'MARKET_IF_TOUCHED':
                cancel_order(o.get('id'))


def send_alert(last_high, last_low, diff, position_size, adj, long_tp, short_tp, trend='no trend'):
    arrow = u'\u2191' if trend == 'up' else (u'\u2193' if trend == 'down' else '')
    contents = {
        'current trend': f"{trend} {arrow}",
        'last 8 hours high': last_high,
        'last 8 hours low': last_low,
        'diff': f'{round(diff * 10000, 4)} pips',
        'position size': position_size,
        'adjustment': f'{adj} pips',
        'buy instruction': f'Buy {position_size} lot at Entry Price: {last_high}, SL: {last_low}, TP: {round(long_tp, 5)}',
        'sell instruction': f'Sell {position_size} lot at Entry Price: {last_low}, SL: {last_high}, TP: {round(short_tp, 5)}'
    }
    css = """
    <style>
        table {
          border-collapse: collapse;
          width: 100%;
        }

        th, td {
          text-align: left;
          padding: 8px;
        }

        tr:nth-child(even) {background-color: #f2f2f2;}
    </style>
    """
    html = '<table border=1><tbody>'
    for name, value in contents.items():
        html += f'<tr><td>{name}</td><td>{value}</td></tr>'
    html += "</tbody></table>"
    notify(f"Trading instructions on {datetime.today().strftime('%Y-%m-%d')}", css + html)


def get_risk_pct(trans):
    """
    Follow 1, 3, 2, 4 stakes sequence
    for example:
        last 4 trades are [win, win, lost, win]
        risk pct 3%
        last 4 trades are [win, win, lost, lost]
        risk pct 1%
    :type trans: list of transactions
        [{'id': '574', 'pl': '-312.0975'}, {'id': '580', 'pl': '367.7076'}, {'id': '588', 'pl': '-310.7640'}]
    :return: float
    """
    last_4_trans = trans[-4:]
    logging.info(f"last 4 transactions:\n{last_4_trans}")
    if last_4_trans:
        last_4_trans.reverse()
        for idx, tran in enumerate(last_4_trans):
            if tran.get('pl') < 0:
                if idx == 0:
                    return 0.01
                elif idx == 1:
                    return 0.03
                elif idx == 2:
                    return 0.02
                elif idx == 3:
                    return 0.04
        if len(last_4_trans) == 1:
            return 0.03
        elif len(last_4_trans) == 2:
            return 0.02
        elif len(last_4_trans) == 3:
            return 0.04
    return 0.01


def run(live_run=False):
    if datetime.today().weekday() in (5, 6):
        logging.info("Do not run over the weekend!")
        return
    # 5 pips adjustment for TP
    param = {
        "count": 120,
        "granularity": "H1"
    }
    resp = api_request(instrument='GBP_USD', p=param)
    df = pd.DataFrame(transform(resp['candles'])).set_index('time')
    logging.info(df)

    trend_up = trending_up(df['close'], TREND_WINDOW).iloc[-1]
    trend_down = trending_down(df['close'], TREND_WINDOW).iloc[-1]

    trend = 'up' if trend_up else ('down' if trend_down else 'no trend')

    df[f'last_{LOOK_BACK_HOURS}_high'] = df['high'].rolling(LOOK_BACK_HOURS).max()
    df[f'last_{LOOK_BACK_HOURS}_low'] = df['low'].rolling(LOOK_BACK_HOURS).min()

    last_high = df[f'last_{LOOK_BACK_HOURS}_high'][-1]
    last_low = df[f'last_{LOOK_BACK_HOURS}_low'][-1]
    diff = last_high - last_low
    logging.info(f'Calculating position size for sl pips {diff}')
    position_size = pos_size(account_balance=10000, risk_pct=0.025, sl_pips=diff * 10000, instrument='GBP_USD')

    long_tp = last_high + diff + ADJUSTMENT
    short_tp = last_low - diff - ADJUSTMENT
    logging.info(f'Placing {position_size} lot buy order. Price: {last_high}, TP: {long_tp}, SL: {last_low}')
    logging.info(f'Placing {position_size} lot sell order. Price: {last_low}, TP: {short_tp}, SL: {last_high}')

    send_alert(last_high, last_low, diff, position_size, 5, long_tp, short_tp, trend)

    if live_run:
        try:
            cancel_pending_orders()
            trans = get_trans(100)
            risk_pct = get_risk_pct(trans=[{'id': t.get('id'), 'pl': float(t.get('pl'))} for t in trans if t.get('pl') and t.get('pl') != '0.0000'])
            logging.info(f'Risk percent is {risk_pct}')
            position_size = pos_size(account_balance=10000, risk_pct=risk_pct, sl_pips=diff * 10000, instrument='GBP_USD')
            logging.info(f'Position size is {position_size}')
            placing_order(order_type=OrderType.MARKET_IF_TOUCHED, instrument='GBP_USD', side='buy', units=100000 * position_size, price=last_high, tp=long_tp, sl=last_low)
            placing_order(order_type=OrderType.MARKET_IF_TOUCHED, instrument='GBP_USD', side='sell', units=100000 * position_size, price=last_low, tp=short_tp, sl=last_high)
        except Exception as ex:
            logging.error(f"Failed to place order with error:\n{ex}")
    else:
        logging.info('Dry run only for testing.')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="London Breakout Strategy")
    parser.add_argument("--liveRun", help="Flag to indicate dry or live run", action='store_true', default=False)
    args = parser.parse_args()
    run(args.liveRun)
