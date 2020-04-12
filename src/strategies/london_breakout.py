import logging
import math
from datetime import timedelta, datetime

import pandas as pd
from matplotlib import pyplot as plt

from src.backtester import BackTester
from src.common import read_price_df
from src.indicators import wma
from src.order_utils.order import OrderStatus, Order

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


plt.style.use('ggplot')


def plot_performance(strats: list, tp_adjustments: tuple):
    df = pd.DataFrame({'date': [el.order_date for el in strats[0]]}).set_index('date')
    for idx, adj in enumerate(tp_adjustments):
        df[f'pnl_{adj}'] = [round(el.pnl, 4) for el in strats[idx]]

    grp_by = df.groupby(['date']).sum()
    for adj in tp_adjustments:
        grp_by[f'cumsum_{adj}'] = grp_by[f'pnl_{adj}'].cumsum()

    logging.info(grp_by)
    grp_by[[header for header in grp_by.columns if header.startswith('cumsum')]].plot()
    plt.show()


def create_orders(price_data, adj=0.0, verify_ema=False):
    """
    params: price_data, list of dictionaries
    params: adj: float, to adjust the TP
    return: list of orders
    """
    orders = []

    for time, ohlc in price_data.items():
        # x - (y - x) = 2x - y
        if math.isnan(ohlc['last_8_high']):
            continue

        if time.hour == 8:
            for order in [el for el in orders if el.status == OrderStatus.PENDING]:
                order.status = OrderStatus.CANCELLED

            buy_tp = round(ohlc['last_8_high'] * 2 - ohlc['last_8_low'] + adj, 5)
            buy_sl = ohlc['last_8_low']
            sell_tp = round(ohlc['last_8_low'] * 2 - ohlc['last_8_high'] - adj, 5)
            sell_sl = ohlc['last_8_high']

            if verify_ema:
                if ohlc['low'] >= ohlc['ema']:
                    orders.append(Order(time, 'long', ohlc['last_8_high'], buy_sl, buy_tp, 0, OrderStatus.PENDING))
                elif ohlc['high'] <= ohlc['ema']:
                    orders.append(Order(time, 'short', ohlc['last_8_low'], sell_sl, sell_tp, 0, OrderStatus.PENDING))
            else:
                orders.append(Order(time, 'long', ohlc['last_8_high'], buy_sl, buy_tp, 0, OrderStatus.PENDING))
                orders.append(Order(time, 'short', ohlc['last_8_low'], sell_sl, sell_tp, 0, OrderStatus.PENDING))

        for order in orders:
            # Try to fill pending orders
            if order.status == OrderStatus.PENDING:
                if order.is_long:
                    if ohlc['high'] > order.entry:  # buy order filled
                        order.fill(time)
                elif order.is_short:
                    if ohlc['low'] < order.entry:  # sell order filled
                        order.fill(time)

    logging.info(f'{len(orders)} orders created.')
    return orders


if __name__ == "__main__":
    from_date = datetime(2015, 1, 1)
    last_date = datetime(2020, 3, 31)

    logging.info(f'Reading date between {from_date} and {last_date}')
    df = read_price_df(instrument='EUR_USD', granularity='H1', start=from_date, end=last_date)

    df['last_8_high'] = df['high'].rolling(8).max()
    df['last_8_low'] = df['low'].rolling(8).min()
    df['diff_pips'] = (df['last_8_high'] - df['last_8_low']) * 10000
    df['ema'] = wma(df['close'], 50)

    logging.info(df[['open', 'high', 'low', 'close', 'last_8_high', 'last_8_low', 'diff_pips', 'ema']])
    back_tester = BackTester()
    dfs = []
    for adj in (0, 5, 10,):
        orders = create_orders(df.to_dict('index'), adj=adj / 10000)
        dfs.append(back_tester.run(df, orders, print_stats=True, suffix=f'_{adj}'))

    orders = create_orders(df.to_dict('index'), adj=5 / 10000, verify_ema=True)
    dfs.append(back_tester.run(df, orders, print_stats=True, suffix='_ema_50'))

    back_tester.plot_chart(dfs)
