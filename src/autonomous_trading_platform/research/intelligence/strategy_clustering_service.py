"""
Strategy clustering service (TASK-2.5).

Groups strategy candidates into clusters using deterministic hierarchical
clustering over normalised feature vectors.  Identifies:

  - duplicate / near-identical strategies (parameter spam)
  - correlated strategy groups (similar regime/factor exposure)
  - regime-specialised clusters
  - component-family clusters

All clustering is deterministic, reproducible, and explainable.
No stochastic algorithms (random seeds, k-means with non-fixed init, etc.)
are used — the algorithm must produce the same clusters from the same inputs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from autonomous_trading_platform.research.intelligence.research_feature_vector_builder import (
    _NEUTRAL,
    ResearchFeatureVector,
)


def _euclidean(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=False)))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return dot / (na * nb)


@dataclass(frozen=True)
class ClusterMember:
    strategy_id: str
    experiment_id: str
    distance_to_centroid: float


@dataclass(frozen=True)
class StrategyCluster:
    """A group of similar strategy candidates."""

    cluster_id: str
    members: tuple[ClusterMember, ...]
    representative_strategy_id: str  # closest member to centroid
    centroid: tuple[float, ...]
    intra_cluster_variance: float  # mean squared distance to centroid
    dominant_family: str | None  # most common family if metadata available
    regime_bias: str | None  # notable regime bias if detectable
    is_parameter_spam: bool  # True if cluster looks like a parameter sweep
    summary: dict[str, Any]

    @property
    def size(self) -> int:
        return len(self.members)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "size": self.size,
            "representative": self.representative_strategy_id,
            "intra_cluster_variance": round(self.intra_cluster_variance, 6),
            "dominant_family": self.dominant_family,
            "regime_bias": self.regime_bias,
            "is_parameter_spam": self.is_parameter_spam,
            "members": [
                {
                    "strategy_id": m.strategy_id,
                    "distance_to_centroid": round(m.distance_to_centroid, 6),
                }
                for m in self.members
            ],
            "summary": self.summary,
        }


class StrategyClusteringService:
    """
    Clusters strategy candidates by feature vector similarity.

    Algorithm
    ---------
    Single-linkage agglomerative hierarchical clustering over Euclidean
    distances on the normalised feature vectors.  The number of clusters
    is determined automatically by a distance threshold, or can be
    specified directly.

    Parameters
    ----------
    distance_threshold
        Two strategies are merged if their Euclidean distance is below this
        value.  Controls granularity.  Default 0.30 (30% of feature scale).
    max_clusters
        Hard cap on cluster count to prevent fragmentation.
    parameter_spam_threshold
        Clusters with intra_cluster_variance below this are flagged as
        potential parameter sweeps.
    """

    def __init__(
        self,
        *,
        distance_threshold: float = 0.30,
        max_clusters: int = 12,
        parameter_spam_threshold: float = 0.02,
    ) -> None:
        self._dist_threshold = distance_threshold
        self._max_clusters = max_clusters
        self._spam_threshold = parameter_spam_threshold

    def cluster(
        self,
        vectors: list[ResearchFeatureVector],
    ) -> list[StrategyCluster]:
        """Cluster candidates and return sorted list (largest cluster first)."""
        if not vectors:
            return []
        if len(vectors) == 1:
            return [self._single_cluster(vectors[0])]

        arrays = {v.strategy_id: v.as_array for v in vectors}
        id_order = [v.strategy_id for v in vectors]

        # Compute pairwise distance matrix
        n = len(id_order)
        dist: dict[tuple[int, int], float] = {}
        for i in range(n):
            for j in range(i + 1, n):
                d = _euclidean(arrays[id_order[i]], arrays[id_order[j]])
                dist[(i, j)] = d
                dist[(j, i)] = d

        # Agglomerative single-linkage
        assignments = list(range(n))  # each point starts in its own cluster
        cluster_count = n

        while cluster_count > 1:
            # Find the closest pair of distinct clusters
            min_dist = float("inf")
            merge_a: int = -1
            merge_b: int = -1
            for i in range(n):
                for j in range(i + 1, n):
                    if assignments[i] == assignments[j]:
                        continue
                    d = dist[(i, j)]
                    if d < min_dist:
                        min_dist = d
                        merge_a = i
                        merge_b = j

            if merge_a < 0 or min_dist >= self._dist_threshold:
                break
            if cluster_count <= 1:
                break

            # Merge: relabel all points in merge_b's cluster to merge_a's cluster
            old_label = assignments[merge_b]
            new_label = assignments[merge_a]
            assignments = [new_label if a == old_label else a for a in assignments]
            cluster_count = len(set(assignments))
            if cluster_count <= max(1, n - self._max_clusters + 1):
                break

        # Group into clusters
        cluster_map: dict[int, list[int]] = {}
        for idx, label in enumerate(assignments):
            cluster_map.setdefault(label, []).append(idx)

        vec_by_id = {v.strategy_id: v for v in vectors}
        clusters = []
        for cluster_idx, (_, member_indices) in enumerate(
            sorted(cluster_map.items(), key=lambda kv: -len(kv[1]))
        ):
            member_ids = [id_order[i] for i in member_indices]
            cluster = self._build_cluster(
                cluster_id=f"cluster_{cluster_idx:02d}",
                member_ids=member_ids,
                arrays=arrays,
                vec_by_id=vec_by_id,
            )
            clusters.append(cluster)

        return clusters

    def pairwise_similarities(
        self,
        vectors: list[ResearchFeatureVector],
    ) -> dict[tuple[str, str], float]:
        """Compute pairwise cosine similarities for all vector pairs."""
        result: dict[tuple[str, str], float] = {}
        for i, vi in enumerate(vectors):
            for j, vj in enumerate(vectors):
                if i >= j:
                    continue
                sim = max(0.0, min(1.0, _cosine_similarity(vi.as_array, vj.as_array)))
                result[(vi.strategy_id, vj.strategy_id)] = sim
                result[(vj.strategy_id, vi.strategy_id)] = sim
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_cluster(
        self,
        cluster_id: str,
        member_ids: list[str],
        arrays: dict[str, list[float]],
        vec_by_id: dict[str, ResearchFeatureVector],
    ) -> StrategyCluster:
        n = len(member_ids)
        dim = len(next(iter(arrays.values())))

        # Centroid: mean of all member vectors
        centroid = [0.0] * dim
        for mid in member_ids:
            for k, v in enumerate(arrays[mid]):
                centroid[k] += v / n

        # Distance to centroid and variance
        dists = {mid: _euclidean(arrays[mid], centroid) for mid in member_ids}
        variance = sum(d * d for d in dists.values()) / n

        # Representative: closest to centroid
        representative = min(dists, key=lambda x: dists[x])

        # Dominant family
        families: dict[str, int] = {}
        family_fields = (
            "family_momentum",
            "family_mean_reversion",
            "family_trend",
            "family_factor",
            "family_composite",
        )
        for mid in member_ids:
            vec = vec_by_id[mid]
            fv = vec.as_dict()
            for ff in family_fields:
                if fv.get(ff, 0.0) >= 0.5:
                    fam = ff.replace("family_", "")
                    families[fam] = families.get(fam, 0) + 1
        dominant_family = max(families, key=lambda k: families[k]) if families else None

        # Regime bias: check if cluster members concentrate on a specific regime field
        regime_bias = self._detect_regime_bias(member_ids, vec_by_id)

        # Parameter spam detection: very small variance = likely parameter sweep
        is_spam = variance < self._spam_threshold and n > 2

        members = tuple(
            ClusterMember(
                strategy_id=mid,
                experiment_id=vec_by_id[mid].experiment_id,
                distance_to_centroid=dists[mid],
            )
            for mid in member_ids
        )

        return StrategyCluster(
            cluster_id=cluster_id,
            members=members,
            representative_strategy_id=representative,
            centroid=tuple(centroid),
            intra_cluster_variance=variance,
            dominant_family=dominant_family,
            regime_bias=regime_bias,
            is_parameter_spam=is_spam,
            summary={
                "size": n,
                "dominant_family": dominant_family,
                "regime_bias": regime_bias,
                "intra_cluster_variance": round(variance, 6),
                "is_parameter_spam": is_spam,
                "avg_distance_to_centroid": round(sum(dists.values()) / n, 6),
            },
        )

    def _detect_regime_bias(
        self,
        member_ids: list[str],
        vec_by_id: dict[str, ResearchFeatureVector],
    ) -> str | None:
        """Return a regime label if the cluster shows a strong systematic regime bias."""
        regime_fields = {
            "regime_trend_robustness_norm": "trend",
            "regime_volatility_robustness_norm": "volatility",
            "regime_liquidity_robustness_norm": "liquidity",
            "regime_mean_reversion_robustness_norm": "mean_reversion",
            "regime_risk_robustness_norm": "risk",
        }
        field_avgs: dict[str, float] = {}
        for ff, label in regime_fields.items():
            vals = [vec_by_id[mid].as_dict().get(ff, _NEUTRAL) for mid in member_ids]
            field_avgs[label] = sum(vals) / len(vals)

        if not field_avgs:
            return None

        max_label = max(field_avgs, key=lambda k: field_avgs[k])
        min_label = min(field_avgs, key=lambda k: field_avgs[k])

        # Report bias if max is high and min is low (regime-specialised)
        if field_avgs[max_label] >= 0.65 and field_avgs[min_label] <= 0.4:
            return f"strong_{max_label}"
        if field_avgs[max_label] >= 0.65:
            return f"favours_{max_label}"
        return None

    def _single_cluster(self, vec: ResearchFeatureVector) -> StrategyCluster:
        arr = vec.as_array
        return StrategyCluster(
            cluster_id="cluster_00",
            members=(
                ClusterMember(
                    strategy_id=vec.strategy_id,
                    experiment_id=vec.experiment_id,
                    distance_to_centroid=0.0,
                ),
            ),
            representative_strategy_id=vec.strategy_id,
            centroid=tuple(arr),
            intra_cluster_variance=0.0,
            dominant_family=None,
            regime_bias=None,
            is_parameter_spam=False,
            summary={"size": 1},
        )
