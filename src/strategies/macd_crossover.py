"""
MACD line crossover strategy
    Setup:
        MACD (12, 26, 9)
        EMA 200
        ATR 14

    Rules:
        Entry:
            1. Placing a long order When:
                1) macd line cross over signal line from bottom
                2) crossover happens below 0
                3) price is above 200 EMA
            2. Placing a short order when:
                1) macd line cross over signal line from top
                2) crossover happens above 0
                3) price is below 200 EMA

        Stop loss:
            long: entry - 14 days ATR
            short: entry + 14 days ATR

        Take profit:
            long: entry + 14 days ATR * 1.5
            short: entry - 14 days ATR * 1.5

        Position size:
            2% risk
"""
from functools import partial
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

from src.backtester import BackTester
from src.finta.ta import TA
from src.common import read_price_df
from src.order_utils.order import Order, OrderSide, OrderStatus


def preparing_price_feed():
    s = datetime(2005, 1, 1)
    e = datetime(2020, 4, 30)
    pd_h1 = read_price_df(instrument='GBP_USD', granularity='H1', start=s, end=e)
    pd_d = read_price_df(instrument='GBP_USD', granularity='D', start=s, end=e)
    pd_h1[['macd', 'signal']] = TA.MACD(pd_h1)
    pd_h1['ema_200'] = TA.EMA(pd_h1, period=200)
    pd_h1['atr'] = TA.ATR(pd_h1)
    pd_h1['rsi'] = TA.RSI(pd_h1)
    pd_d['atr'] = TA.ATR(pd_d)
    pd_d['rsi'] = TA.RSI(pd_d)

    pd_h1.reset_index(level=0, inplace=True)
    pd_h1 = pd_h1.apply(partial(_enrich, pd_d), axis=1).set_index('time')

    print(pd_h1)
    pd_h1.to_csv(f'c:/temp/gbp_usd_macd.csv')


def _enrich(pd_d, row):
    d = pd_d[pd_d.index <= row.time]
    row['day_atr'] = d['atr'][-1]
    row['day_rsi'] = d['rsi'][-1]
    return row


if __name__ == '__main__':
    # preparing_price_feed()
    price_df = pd.read_csv('c:/temp/gbp_usd_macd.csv')
    price_df = price_df[(price_df['time'] >= '2010-01-01') & (price_df['time'] < '2020-04-30')]
    price_df['comparison'] = np.where(price_df['macd'] > price_df['signal'], 1, 0)
    price_df['cross'] = price_df['comparison'].diff()
    price_df['next_open'] = price_df['open'].shift(-1)
    price_df['time'] = pd.to_datetime(price_df['time'])
    # price_df.to_csv('c:/temp/tmp.csv')
    orders = []
    for idx, ohlc in enumerate(price_df.to_dict('records')):
        # Generate buy order: (rules above)
        if ohlc['cross'] == 1 and ohlc['close'] > ohlc['ema_200'] and ohlc['macd'] < 0:
            atr = ohlc['atr']
            entry = ohlc['next_open']
            orders.append(
                Order(
                    order_date=ohlc['time'] + timedelta(hours=1),
                    side=OrderSide.LONG,
                    entry=entry,
                    sl=entry - atr,
                    tp=entry + atr * 1.5,
                    status=OrderStatus.PENDING)
            )
        # Generate sell order: (rules above)
        if ohlc['cross'] == -1 and ohlc['close'] < ohlc['ema_200'] and ohlc['macd'] > 0:
            atr = ohlc['atr']
            entry = ohlc['next_open']
            orders.append(
                Order(
                    order_date=ohlc['time'] + timedelta(hours=1),
                    side=OrderSide.SHORT,
                    entry=entry,
                    sl=entry + atr,
                    tp=entry - atr * 1.5,
                    status=OrderStatus.PENDING)
            )

    backtester = BackTester(strategy='MACD crossover')
    test_orders = backtester.run(price_feed=price_df.set_index('time'), orders=orders, print_stats=True)

    for o in orders:
        print(o)

    backtester.plot_chart([test_orders])