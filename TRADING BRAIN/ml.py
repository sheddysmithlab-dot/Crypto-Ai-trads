"""
Binomial price-direction model — the machine-learning layer.

Inspired by "Automated Bitcoin Trading via Machine Learning Algorithms"
(Isaac Madan, Shaurya Saluja, Aojia Zhao — Stanford CS229). That paper framed
price prediction as *binomial classification* (predict the SIGN of the next
price change) using logistic regression / GLM and random forests, and reported
sensitivity / specificity / precision / accuracy.

This module reproduces that idea in pure Python:
  * feature engineering from OHLCV (returns, RSI, momentum, ATR, MA distance,
    candle anatomy, volatility)
  * logistic regression (gradient descent) and a random forest (CART bagging)
  * the same sensitivity / specificity / precision / accuracy report

No scikit-learn required.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .candles import Candle, CandleSeries, EPS


# --------------------------------------------------------------------------
#  feature engineering
# --------------------------------------------------------------------------
def _std(values: Sequence[float]) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    m = sum(values) / n
    return (sum((x - m) ** 2 for x in values) / n) ** 0.5


def build_features(candles: Sequence[Candle]) -> Tuple[List[List[float]], List[int], List[int]]:
    """Return (X, y, indices). X rows are feature vectors; y is sign(next change)."""
    series = CandleSeries(candles)
    n = len(candles)
    closes = series.closes
    rsi = series.rsi(14)
    atr = series.atr(14)
    sma21 = series.sma(21)
    sma200 = series.sma(200)

    X: List[List[float]] = []
    y: List[int] = []
    indices: List[int] = []

    for i in range(21, n - 1):  # need 21 lookback and 1 forward bar
        c = candles[i]
        ret1 = (closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] else 0.0
        ret3 = (closes[i] - closes[i - 3]) / closes[i - 3] if i >= 3 and closes[i - 3] else 0.0
        ret5 = (closes[i] - closes[i - 5]) / closes[i - 5] if i >= 5 and closes[i - 5] else 0.0
        ret10 = (closes[i] - closes[i - 10]) / closes[i - 10] if i >= 10 and closes[i - 10] else 0.0
        mom10 = closes[i] - closes[i - 10] if i >= 10 else 0.0
        atr_ratio = atr[i] / closes[i] if atr[i] and closes[i] else 0.0
        d21 = (closes[i] - sma21[i]) / closes[i] if sma21[i] and closes[i] else 0.0
        d200 = (closes[i] - sma200[i]) / closes[i] if sma200[i] and closes[i] else 0.0
        vol20 = _std([(closes[k] - closes[k - 1]) / closes[k - 1]
                      for k in range(i - 19, i + 1) if closes[k - 1]])

        features = [
            ret1, ret3, ret5, ret10,
            (rsi[i] - 50.0) / 50.0 if rsi[i] is not None else 0.0,
            mom10 / closes[i] if closes[i] else 0.0,
            atr_ratio, d21, d200,
            c.body_ratio, c.upper_ratio, c.lower_ratio,
            1.0 if c.is_doji(0.1) else 0.0,
            vol20,
        ]
        label = 1 if closes[i + 1] > closes[i] else 0
        X.append(features)
        y.append(label)
        indices.append(i)

    return X, y, indices


# --------------------------------------------------------------------------
#  scaler
# --------------------------------------------------------------------------
class StandardScaler:
    def __init__(self):
        self.mean = []
        self.std = []

    def fit(self, X: Sequence[Sequence[float]]):
        m = len(X)
        n = len(X[0])
        self.mean = [sum(row[j] for row in X) / m for j in range(n)]
        self.std = [_std([row[j] for row in X]) for j in range(n)]
        self.std = [s if s > EPS else 1.0 for s in self.std]
        return self

    def transform(self, X: Sequence[Sequence[float]]) -> List[List[float]]:
        return [[(row[j] - self.mean[j]) / self.std[j] for j in range(len(row))]
                for row in X]


# --------------------------------------------------------------------------
#  logistic regression (gradient descent)
# --------------------------------------------------------------------------
class LogisticRegression:
    def __init__(self, lr: float = 0.1, l2: float = 0.01, max_iter: int = 400):
        self.lr = lr
        self.l2 = l2
        self.max_iter = max_iter
        self.weights: List[float] = []

    @staticmethod
    def _sigmoid(z: float) -> float:
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        e = math.exp(z)
        return e / (1.0 + e)

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int]):
        m = len(X)
        n = len(X[0])
        self.weights = [0.0] * (n + 1)  # + bias
        for _ in range(self.max_iter):
            # forward pass
            probs = []
            for row in X:
                z = self.weights[0] + sum(self.weights[j + 1] * row[j] for j in range(n))
                probs.append(self._sigmoid(z))
            # gradients
            grad = [0.0] * (n + 1)
            for i in range(m):
                err = probs[i] - y[i]
                grad[0] += err
                for j in range(n):
                    grad[j + 1] += err * X[i][j]
            for j in range(n + 1):
                grad[j] = grad[j] / m + (0.0 if j == 0 else self.l2 * self.weights[j])
            # update
            for j in range(n + 1):
                self.weights[j] -= self.lr * grad[j]
        return self

    def predict_proba(self, X: Sequence[Sequence[float]]) -> List[float]:
        n = len(self.weights) - 1
        out = []
        for row in X:
            z = self.weights[0] + sum(self.weights[j + 1] * row[j] for j in range(n))
            out.append(self._sigmoid(z))
        return out

    def predict(self, X: Sequence[Sequence[float]]) -> List[int]:
        return [1 if p >= 0.5 else 0 for p in self.predict_proba(X)]


# --------------------------------------------------------------------------
#  decision tree (CART) + random forest
# --------------------------------------------------------------------------
class _TreeNode:
    def __init__(self):
        self.feature: Optional[int] = None
        self.threshold: Optional[float] = None
        self.left = None
        self.right = None
        self.value: int = 0
        self.is_leaf = True


def _gini(y: Sequence[int]) -> float:
    m = len(y)
    if m == 0:
        return 0.0
    p1 = sum(y) / m
    p0 = 1.0 - p1
    return 1.0 - (p0 * p0 + p1 * p1)


class DecisionTree:
    def __init__(self, max_depth: int = 4, min_samples_split: int = 5,
                 max_features: Optional[int] = None, seed: int = 0):
        self.max_depth = max_depth
        self.min_samples_split = min_samples_split
        self.max_features = max_features
        self.rng = random.Random(seed)
        self.root = None

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int]):
        self.root = self._build(list(range(len(X))), X, y, 0)
        return self

    def _feature_pool(self, n_features: int) -> List[int]:
        if self.max_features is None or self.max_features >= n_features:
            return list(range(n_features))
        return self.rng.sample(range(n_features), self.max_features)

    def _build(self, idx: List[int], X, y, depth: int) -> _TreeNode:
        node = _TreeNode()
        node.value = 1 if sum(y[i] for i in idx) * 2 >= len(idx) else 0
        if depth >= self.max_depth or len(idx) < self.min_samples_split or _gini([y[i] for i in idx]) == 0.0:
            return node

        n_features = len(X[0])
        best = None  # (gini_gain, feature, threshold, left_idx, right_idx)
        for f in self._feature_pool(n_features):
            values = sorted({X[i][f] for i in idx})
            if len(values) < 2:
                continue
            for k in range(len(values) - 1):
                thr = (values[k] + values[k + 1]) / 2.0
                left = [i for i in idx if X[i][f] <= thr]
                right = [i for i in idx if X[i][f] > thr]
                if not left or not right:
                    continue
                g = (len(left) / len(idx)) * _gini([y[i] for i in left]) + \
                    (len(right) / len(idx)) * _gini([y[i] for i in right])
                if best is None or g < best[0]:
                    best = (g, f, thr, left, right)

        if best is None:
            return node
        _, f, thr, left, right = best
        node.is_leaf = False
        node.feature = f
        node.threshold = thr
        node.left = self._build(left, X, y, depth + 1)
        node.right = self._build(right, X, y, depth + 1)
        return node

    def _predict_one(self, node: _TreeNode, row: Sequence[float]) -> int:
        while not node.is_leaf:
            if row[node.feature] <= node.threshold:
                node = node.left
            else:
                node = node.right
        return node.value

    def predict(self, X: Sequence[Sequence[float]]) -> List[int]:
        return [self._predict_one(self.root, row) for row in X]


class RandomForest:
    def __init__(self, n_estimators: int = 15, max_depth: int = 4,
                 min_samples_split: int = 5, seed: int = 0):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.min_samples_split = min_samples_split
        self.seed = seed
        self.trees: List[DecisionTree] = []

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int]):
        m = len(X)
        n = len(X[0])
        max_features = max(1, int(math.sqrt(n)))
        for t in range(self.n_estimators):
            rng = random.Random(self.seed + t)
            # bootstrap sample
            sample_idx = [rng.randrange(m) for _ in range(m)]
            Xb = [X[i] for i in sample_idx]
            yb = [y[i] for i in sample_idx]
            tree = DecisionTree(self.max_depth, self.min_samples_split,
                                max_features, seed=self.seed + t)
            tree.fit(Xb, yb)
            self.trees.append(tree)
        return self

    def predict(self, X: Sequence[Sequence[float]]) -> List[int]:
        votes = [0] * len(X)
        for tree in self.trees:
            preds = tree.predict(X)
            for i, p in enumerate(preds):
                votes[i] += 1 if p == 1 else -1
        return [1 if v >= 0 else 0 for v in votes]


# --------------------------------------------------------------------------
#  metrics (matching the Madan paper's vocabulary)
# --------------------------------------------------------------------------
def classification_report(y_true: Sequence[int], y_pred: Sequence[int]) -> dict:
    tp = tn = fp = fn = 0
    for a, b in zip(y_true, y_pred):
        if a == 1 and b == 1:
            tp += 1
        elif a == 0 and b == 0:
            tn += 1
        elif a == 0 and b == 1:
            fp += 1
        elif a == 1 and b == 0:
            fn += 1
    accuracy = (tp + tn) / len(y_true) if y_true else 0.0
    sensitivity = tp / (tp + fn) if (tp + fn) else 0.0   # TPR
    specificity = tn / (tn + fp) if (tn + fp) else 0.0   # TNR
    precision = tp / (tp + fp) if (tp + fp) else 0.0     # PPV
    return {
        "accuracy": round(accuracy, 4),
        "sensitivity": round(sensitivity, 4),
        "specificity": round(specificity, 4),
        "precision": round(precision, 4),
        "n": len(y_true),
    }


def train_test_split(X, y, test_ratio: float = 0.3) -> Tuple:
    """Chronological split (no shuffling — respects time order)."""
    split = int(len(X) * (1 - test_ratio))
    return X[:split], X[split:], y[:split], y[split:]


# --------------------------------------------------------------------------
#  high-level model
# --------------------------------------------------------------------------
class PriceDirectionModel:
    """Predicts the sign of the next price change (binomial classification)."""

    def __init__(self, model: str = "logistic"):
        if model not in ("logistic", "forest"):
            raise ValueError("model must be 'logistic' or 'forest'")
        self.model_name = model
        self.model = None
        self.scaler = StandardScaler()
        self.metrics: dict = {}
        self.horizon: int = 1

    def fit(self, candles: Sequence[Candle], horizon: int = 1,
            test_ratio: float = 0.3):
        self.horizon = horizon
        X, y, idx = build_features(candles)
        if len(X) < 50:
            raise ValueError("not enough data to train (need >= 50 feature rows)")
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_ratio)
        self.scaler.fit(Xtr)
        Xtr_s = self.scaler.transform(Xtr)
        Xte_s = self.scaler.transform(Xte)
        if self.model_name == "logistic":
            self.model = LogisticRegression()
        else:
            self.model = RandomForest()
        self.model.fit(Xtr_s, ytr)
        preds = self.model.predict(Xte_s)
        self.metrics = classification_report(yte, preds)
        return self

    def predict_latest(self, candles: Sequence[Candle]) -> dict:
        """Classify the most recent candle: will the next bar close higher?"""
        X, y, idx = build_features(candles)
        if not X:
            return {"error": "not enough data"}
        latest = X[-1]
        latest_s = self.scaler.transform([latest])
        proba = 1.0
        if hasattr(self.model, "predict_proba"):
            proba = self.model.predict_proba(latest_s)[0]
        label = self.model.predict(latest_s)[0]
        return {
            "label": "UP" if label == 1 else "DOWN",
            "probability_up": round(proba, 4),
            "index": idx[-1],
        }
