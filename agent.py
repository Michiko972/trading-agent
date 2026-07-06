//@version=6
indicator('Agent Trading — Stratégie EMA200 (Stoch50 + RSI + ADX)', overlay=true, max_bars_back=5000)

// ══════════════════════════════════════════════
// CONFIRMATIONS — EMA200, RSI(50)+SMA(50), ADX(33)
// ══════════════════════════════════════════════
ema_length  = input.int(200, "EMA Length", group='Confirmation')
rsi_length  = input.int(50,  "RSI Length", group='Confirmation')
rsi_sma_len = input.int(50,  "RSI SMA Length (= période RSI)", group='Confirmation')
adx_length  = input.int(33,  "ADX Length", group='Confirmation')
min_adx     = input.int(15,  "Minimum ADX", group='Confirmation')

ema200 = ta.ema(close, ema_length)
price_above_ema = close > ema200
price_below_ema = close < ema200

rsi    = ta.rsi(close, rsi_length)
rsi_ma = ta.sma(rsi, rsi_sma_len)
rsi_bull = (rsi > rsi_ma) and (rsi > 50)
rsi_bear = (rsi < rsi_ma) and (rsi < 50)

up_dmi   = ta.change(high)
down_dmi = -ta.change(low)
plusDM   = na(up_dmi)   ? na : (up_dmi > down_dmi   and up_dmi > 0   ? up_dmi   : 0)
minusDM  = na(down_dmi) ? na : (down_dmi > up_dmi   and down_dmi > 0 ? down_dmi : 0)
trur     = ta.rma(ta.tr, adx_length)
di_plus  = fixnan(100 * ta.rma(plusDM,  adx_length) / trur)
di_minus = fixnan(100 * ta.rma(minusDM, adx_length) / trur)
dmi_sum  = di_plus + di_minus
adx      = 100 * ta.rma(math.abs(di_plus - di_minus) / (dmi_sum == 0 ? 1 : dmi_sum), adx_length)
adx_ok   = adx > min_adx

plot(ema200, title="EMA 200", color=color.yellow, linewidth=2)

// ══════════════════════════════════════════════
// DÉCLENCHEMENT — Stoch(50), croisement ponctuel de 50
// ══════════════════════════════════════════════
stoch_k_trigger = input.int(50, "Stoch Déclenchement Length", group='Déclenchement')
k_trigger = ta.sma(ta.stoch(close, high, low, stoch_k_trigger), 1)

trigger_long  = ta.crossover(k_trigger, 50)
trigger_short = ta.crossunder(k_trigger, 50)

// ══════════════════════════════════════════════
// SIGNAL FINAL D'ENTRÉE
// ══════════════════════════════════════════════
long_entry  = trigger_long  and price_above_ema and rsi_bull and adx_ok
short_entry = trigger_short and price_below_ema and rsi_bear and adx_ok

plotshape(long_entry,  title="LONG",  style=shape.triangleup,   location=location.belowbar, color=color.lime, size=size.normal)
plotshape(short_entry, title="SHORT", style=shape.triangledown, location=location.abovebar, color=color.red,  size=size.normal)

plot(k_trigger, title="Stoch Déclenchement (50)", color=color.orange,  display=display.data_window)
plot(rsi,       title="RSI (50)",                  color=color.aqua,    display=display.data_window)
plot(adx,       title="ADX (33)",                  color=color.fuchsia, display=display.data_window)

// ══════════════════════════════════════════════
// ALERTES JSON — entrées uniquement (sortie = TP/SL côté agent)
// ══════════════════════════════════════════════
if long_entry
    alert(
     '{"signal":"long",' +
     '"epic":"' + syminfo.ticker + '",' +
     '"stoch_k":' + str.tostring(k_trigger, "#.##") + ',' +
     '"adx":' + str.tostring(adx, "#.##") + ',' +
     '"price":' + str.tostring(close, "#.##") +
     '}',
     alert.freq_once_per_bar_close)

if short_entry
    alert(
     '{"signal":"short",' +
     '"epic":"' + syminfo.ticker + '",' +
     '"stoch_k":' + str.tostring(k_trigger, "#.##") + ',' +
     '"adx":' + str.tostring(adx, "#.##") + ',' +
     '"price":' + str.tostring(close, "#.##") +
     '}',
     alert.freq_once_per_bar_close)
