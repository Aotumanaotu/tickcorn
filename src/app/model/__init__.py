"""Phase 2 -- prediction models (NOT YET IMPLEMENTED).

Deliberately empty until Phase-1 results are reviewed.

Planned scope (per project spec):
    * Prediction target: direction of the NEXT GENUINE QUOTE MOVE
      (Y = +1 up / -1 down / optionally 0 = none within horizon),
      NEVER raw LastPrice up/down (that would mostly learn bid-ask bounce).
    * Horizons: 1/2/3/5 snapshots and ~0.5s/1s/2s/3s measured with real
      snapshot timestamps (snapshots are not uniformly spaced).
    * Baselines first: Random, Majority Class, Logistic Regression,
      then LightGBM. No LSTM/Transformer/RL until simple models show
      a real edge.
    * Inputs: OBI(1/3/5), microprice deviation, spread, volume/turnover
      deltas, order-book changes, short momentum, volatility, time-of-day.
    * Metrics: Accuracy, Balanced Accuracy, Precision/Recall/F1, ROC-AUC,
      probability calibration and hit-rate by confidence threshold.
    * STRICT time-based splits only (no random train_test_split);
      scaler / feature selection / fitting fitted on training data only;
      walk-forward validation.
"""
