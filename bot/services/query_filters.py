"""SQL WHERE-clause builders for outcome and indicator filters."""

def append_post_signal_outcome_filters(req_args, where_clauses, params):
    """Apply 1h/4h Peak Multiplier, Trajectory Path, and Pre-Peak Drawdown outcome filters."""
    outcome_filter = (req_args.get('outcome_filter') or 'all').strip().lower()
    path_filter = (req_args.get('path_filter') or 'all').strip().upper()
    min_peak_1h = (req_args.get('min_peak_1h') or '').strip()
    min_peak_4h = (req_args.get('min_peak_4h') or '').strip()
    max_pre_dd = (req_args.get('max_pre_dd') or '').strip()

    if outcome_filter and outcome_filter != 'all':
        if outcome_filter == 'winner_any_2x':
            where_clauses.append("(COALESCE(peak_mult_1h, 0) >= 2.0 OR COALESCE(peak_mult_4h, 0) >= 2.0)")
        elif outcome_filter == 'win_1h_1_5x':
            where_clauses.append("COALESCE(peak_mult_1h, 0) >= 1.5")
        elif outcome_filter == 'win_1h_2x':
            where_clauses.append("COALESCE(peak_mult_1h, 0) >= 2.0")
        elif outcome_filter == 'win_1h_3x':
            where_clauses.append("COALESCE(peak_mult_1h, 0) >= 3.0")
        elif outcome_filter == 'win_1h_5x':
            where_clauses.append("COALESCE(peak_mult_1h, 0) >= 5.0")
        elif outcome_filter == 'win_1h_10x':
            where_clauses.append("COALESCE(peak_mult_1h, 0) >= 10.0")
        elif outcome_filter == 'win_4h_1_5x':
            where_clauses.append("COALESCE(peak_mult_4h, 0) >= 1.5")
        elif outcome_filter == 'win_4h_2x':
            where_clauses.append("COALESCE(peak_mult_4h, 0) >= 2.0")
        elif outcome_filter == 'win_4h_3x':
            where_clauses.append("COALESCE(peak_mult_4h, 0) >= 3.0")
        elif outcome_filter == 'win_4h_5x':
            where_clauses.append("COALESCE(peak_mult_4h, 0) >= 5.0")
        elif outcome_filter == 'win_4h_10x':
            where_clauses.append("COALESCE(peak_mult_4h, 0) >= 10.0")
        elif outcome_filter == 'clean_pump_2x':
            where_clauses.append("((COALESCE(peak_mult_1h, 0) >= 2.0 AND COALESCE(pre_peak_dd_1h_pct, 0) >= -15.0) OR (COALESCE(peak_mult_4h, 0) >= 2.0 AND COALESCE(pre_peak_dd_4h_pct, 0) >= -15.0))")
        elif outcome_filter == 'dumped':
            where_clauses.append("(peak_mult_1h IS NOT NULL AND COALESCE(peak_mult_1h, 0) < 1.15 AND COALESCE(peak_mult_4h, peak_mult_1h, 0) < 1.15)")
        elif outcome_filter == 'scanned_only':
            where_clauses.append("(peak_mult_1h IS NOT NULL OR peak_mult_4h IS NOT NULL)")

    if path_filter and path_filter != 'ALL':
        where_clauses.append("(UPPER(COALESCE(path_1h, '')) = ? OR UPPER(COALESCE(path_4h, '')) = ?)")
        params.extend([path_filter, path_filter])

    if min_peak_1h:
        try:
            where_clauses.append("COALESCE(peak_mult_1h, 0) >= ?")
            params.append(float(min_peak_1h))
        except ValueError:
            pass

    if min_peak_4h:
        try:
            where_clauses.append("COALESCE(peak_mult_4h, 0) >= ?")
            params.append(float(min_peak_4h))
        except ValueError:
            pass

    if max_pre_dd:
        try:
            dd_val = float(max_pre_dd)
            if dd_val > 0:
                dd_val = -dd_val
            where_clauses.append("((peak_mult_1h IS NOT NULL AND COALESCE(pre_peak_dd_1h_pct, 0) >= ?) OR (peak_mult_4h IS NOT NULL AND COALESCE(pre_peak_dd_4h_pct, 0) >= ?))")
            params.extend([dd_val, dd_val])
        except ValueError:
            pass



def append_akoueflow_indicator_filters(req_args, where_clauses, params):
    """Apply Akoueflow On-Chain and Order Flow trading indicator filters."""
    net_buy_filter = (req_args.get('net_buy_filter') or 'all').strip().lower()
    if net_buy_filter == 'positive':
        where_clauses.append("(COALESCE(get_indicator(metrics_data, 'net_buy_pressure'), 0) > 0)")
    elif net_buy_filter == 'strong_buy':
        where_clauses.append("(COALESCE(get_indicator(metrics_data, 'net_buy_pressure'), 0) >= 10000 AND COALESCE(get_indicator(metrics_data, 'buy_dominance_pct'), 50) >= 60)")
    elif net_buy_filter == 'negative':
        where_clauses.append("(COALESCE(get_indicator(metrics_data, 'net_buy_pressure'), 0) < 0)")

    ind_preset = (req_args.get('ind_preset') or req_args.get('indicator_preset') or 'all').strip().lower()
    min_net_buy = req_args.get('min_net_buy')
    max_net_buy = req_args.get('max_net_buy')
    min_buy_dom = req_args.get('min_buy_dom')
    min_buy_accel = req_args.get('min_buy_accel')
    min_bs_ratio = req_args.get('min_bs_ratio')
    min_buy_liq = req_args.get('min_buy_liq')
    min_vol_mc = req_args.get('min_vol_mc')
    min_vol_liq = req_args.get('min_vol_liq')
    max_holder_conc = req_args.get('max_holder_conc')
    max_round_trip = req_args.get('max_round_trip')

    if min_net_buy:
        try:
            where_clauses.append("get_indicator(metrics_data, 'net_buy_pressure') >= ?")
            params.append(float(min_net_buy))
        except ValueError:
            pass
    if max_net_buy:
        try:
            where_clauses.append("get_indicator(metrics_data, 'net_buy_pressure') <= ?")
            params.append(float(max_net_buy))
        except ValueError:
            pass

    if min_buy_dom:
        try:
            where_clauses.append("get_indicator(metrics_data, 'buy_dominance_pct') >= ?")
            params.append(float(min_buy_dom))
        except ValueError:
            pass

    if min_buy_accel:
        try:
            where_clauses.append("get_indicator(metrics_data, 'buy_vol_accel') >= ?")
            params.append(float(min_buy_accel))
        except ValueError:
            pass

    if min_bs_ratio:
        try:
            where_clauses.append("get_indicator(metrics_data, 'buyer_seller_ratio') >= ?")
            params.append(float(min_bs_ratio))
        except ValueError:
            pass

    if min_buy_liq:
        try:
            where_clauses.append("get_indicator(metrics_data, 'net_buy_to_liq') >= ?")
            params.append(float(min_buy_liq))
        except ValueError:
            pass

    if min_vol_mc:
        try:
            where_clauses.append("get_indicator(metrics_data, 'vol_to_mc') >= ?")
            params.append(float(min_vol_mc))
        except ValueError:
            pass

    if min_vol_liq:
        try:
            where_clauses.append("get_indicator(metrics_data, 'vol_to_liq') >= ?")
            params.append(float(min_vol_liq))
        except ValueError:
            pass

    if max_holder_conc:
        try:
            where_clauses.append("get_indicator(metrics_data, 'holder_concentration') <= ?")
            params.append(float(max_holder_conc))
        except ValueError:
            pass

    if max_round_trip:
        try:
            where_clauses.append("get_indicator(metrics_data, 'round_trip_cost') <= ?")
            params.append(float(max_round_trip))
        except ValueError:
            pass

    # Launch metrics: pump.fun bonding curve progress (%) and dev prior token count
    launch_specs = (
        ('min_bonding_curve', 'bonding_curve_pct', '>='),
        ('max_bonding_curve', 'bonding_curve_pct', '<='),
        ('min_dev_tokens', 'dev_prior_tokens', '>='),
        ('max_dev_tokens', 'dev_prior_tokens', '<='),
    )
    for arg_name, metric_key, op in launch_specs:
        raw = req_args.get(arg_name)
        if raw is None or str(raw).strip() == '':
            continue
        try:
            num = float(raw)
        except (TypeError, ValueError):
            continue
        where_clauses.append(f"get_token_metric(metrics_data, formatted_message, '{metric_key}') {op} ?")
        params.append(num)

    if ind_preset == 'strong_bullish':
        where_clauses.append("get_indicator(metrics_data, 'net_buy_pressure') >= 5000 AND get_indicator(metrics_data, 'buy_dominance_pct') >= 60")
    elif ind_preset == 'buy_surge':
        where_clauses.append("get_indicator(metrics_data, 'buy_vol_accel') >= 2.0 AND get_indicator(metrics_data, 'buy_dominance_pct') >= 55")
    elif ind_preset == 'high_breadth':
        where_clauses.append("get_indicator(metrics_data, 'buyer_seller_ratio') >= 1.5")
    elif ind_preset == 'heavy_turnover':
        where_clauses.append("get_indicator(metrics_data, 'net_buy_to_liq') >= 5.0")
    elif ind_preset == 'low_risk':
        where_clauses.append("get_indicator(metrics_data, 'holder_concentration') <= 35 AND get_indicator(metrics_data, 'round_trip_cost') <= 6.0")
    elif ind_preset == 'heavy_selling':
        where_clauses.append("(get_indicator(metrics_data, 'net_buy_pressure') < 0 OR get_indicator(metrics_data, 'buy_dominance_pct') < 40)")
