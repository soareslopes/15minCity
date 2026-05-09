# util_maps.py
# PNG map generation for city accessibility metrics.

import warnings

import contextily as ctx
import geopandas as gpd
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore")

_POSITRON = ctx.providers.CartoDB.Positron


def make_accessibility_map(
    hex_gdf,
    boundary_geom,
    metric_col,
    output_path,
    city_name,
    cmap="YlOrRd",
):
    """
    Save a PNG map of hexagons coloured by an accessibility metric,
    with a Positron basemap and municipality boundary.

    Parameters
    ----------
    hex_gdf       : GeoDataFrame — hexagons with metric column
    boundary_geom : Shapely geometry (WGS84) — municipality boundary
    metric_col    : str — column name to colour by
    output_path   : Path — destination .png file
    city_name     : str — map title
    cmap          : str — matplotlib colormap name
    """
    if metric_col not in hex_gdf.columns:
        print(f"     WARNING: column '{metric_col}' not found — skipping map")
        return

    hex_web = hex_gdf[[metric_col, "geometry"]].to_crs(epsg=3857)
    boundary_web = (
        gpd.GeoDataFrame({"geometry": [boundary_geom]}, crs="EPSG:4326")
        .to_crs(epsg=3857)
    )

    fig, ax = plt.subplots(figsize=(12, 12))

    hex_web.plot(
        column=metric_col,
        ax=ax,
        cmap=cmap,
        alpha=0.78,
        edgecolor="#333333",
        linewidth=0.3,
        legend=True,
        legend_kwds={
            "label": metric_col.replace("_", " ").title(),
            "orientation": "horizontal",
            "shrink": 0.55,
            "pad": 0.03,
        },
        missing_kwds={"color": "#dddddd", "edgecolor": "#bbbbbb", "label": "Sem dados"},
    )

    boundary_web.boundary.plot(ax=ax, color="black", linewidth=2.5, zorder=5)

    ctx.add_basemap(ax, source=_POSITRON, zoom="auto", attribution_size=7)

    _METRIC_LABELS = {
        "total_dest": "Total Destinations Accessible by Walking",
        "variety":    "Variety of Destination Types Accessible by Walking",
        "entropy":    "Destination Type Entropy (Walking)",
    }

    # Parse metric name and time interval from column like "total_dest_30min"
    parts = metric_col.rsplit("_", 1)
    interval = parts[-1] if parts[-1].endswith("min") else ""
    base = metric_col[: -len(interval) - 1] if interval else metric_col
    metric_label = _METRIC_LABELS.get(base, base.replace("_", " ").title())
    subtitle = f"{metric_label} — within {interval}" if interval else metric_label

    ax.set_axis_off()
    ax.set_title(
        f"{city_name}\n{subtitle}",
        fontsize=13,
        fontweight="bold",
        pad=10,
        linespacing=1.6,
    )

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"     Map saved: {output_path.name}")
