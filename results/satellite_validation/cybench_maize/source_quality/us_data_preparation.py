import pandas as pd

data_path = "C:/Users/paude006/Documents/git-repos/AgML-crop-yield-forecasting/data/data_US"

# Data were downloaded into 2 CSV files because NASS limits number of entries in the query result.
# NOTE: The downloaded data includes years 1995-2023.
# We started from 1995 for two reasons:
# 1. Remote sensing data starts from 2000.
# 2. Data from 1995 to 1999 is useful in case we use yield trend (trend window 5).
# It is possible to download yield statistics from earlier years if necessary.
# Change the years in nass_stats.R. Downloading too many years will cause hit the NASS limit.
# Then you can download in multiple runs of the script with different year ranges.
filename1 = "nass_stats1.csv"
filename2 = "nass_stats2.csv"
crop_stats_df1 = pd.read_csv(data_path + "/" + filename1,
                             delimiter=",",
                             # set all to str, some columns have mixed types (e.g. str and nan)
                             dtype="str",
                             header=0)
# print("\n")
# print(crop_stats_df1.head(5).to_string())
# print(crop_stats_df1["Year"].min(), crop_stats_df1["Year"].max())

crop_stats_df2 = pd.read_csv(data_path + "/" + filename2,
                             delimiter=",",
                             # set all to str, some columns have mixed types (e.g. str and nan)
                             dtype="str",
                             header=0)
# print("\n")
# print(crop_stats_df2.head(5).to_string())
# print(crop_stats_df2["Year"].min(), crop_stats_df2["Year"].max())

crop_stats_df = pd.concat([crop_stats_df1, crop_stats_df2], axis=0)

# set YEAR and VALUE to numeric
crop_stats_df = crop_stats_df.astype({"Year" : "int64", "Yield" : "float64"})
print("\n")
crop_stats_df["adm_id"] = "US-" + crop_stats_df["statefp"] + "-" + crop_stats_df["countyfp"] 
print(crop_stats_df.head(5).to_string())
crops = crop_stats_df["Crop"].unique()
print("\n")
print("Crops", crops)

selected_crops = ["corn_grain ", "wheat_winter "]
crop_stats_df = crop_stats_df[crop_stats_df["Crop"].isin(selected_crops)]

for cr in selected_crops:
  print("\n")
  print("Crop:", cr)
  print("---------------------")
  print(crop_stats_df[crop_stats_df["Crop"] == cr].head(5).to_string())
def getCropCountrySummary(crop, yield_df, adm_id_col, year_col):
  countries_summary = {}
  countries = yield_df[adm_id_col].str[:2].unique()
  row_idx = 0
  column_names = ["crop_name", "country_code", "min_year", "max_year", "num_years",
                  "num_regions", "data_size"]
  for cn in countries:
    yield_cn_df = yield_df[yield_df[adm_id_col].str[:2] == cn]
    if (len(yield_cn_df.index) <= 1):
      continue

    min_year = yield_cn_df[year_col].min()
    max_year = yield_cn_df[year_col].max()
    num_years = len(yield_cn_df[year_col].unique())
    num_regions = yield_cn_df[yield_cn_df[year_col] == max_year][adm_id_col].count()
    data_size = yield_cn_df[year_col].count()
    countries_summary["row" + str(row_idx)] = [crop, cn, min_year, max_year, num_years,
                                              num_regions, data_size]
    row_idx += 1

  return countries_summary, column_names
crop = "wheat_winter "
crop_yield_df = crop_stats_df[crop_stats_df["Crop"] == crop]
countries_summary, column_names = getCropCountrySummary(crop, crop_yield_df, "adm_id", "Year")
countries_summary_df = pd.DataFrame.from_dict(countries_summary, columns=column_names,
                                              orient="index")
print(countries_summary_df.head(30).to_string())
crop = "corn_grain "
crop_yield_df = crop_stats_df[crop_stats_df["Crop"] == crop]
countries_summary, column_names = getCropCountrySummary(crop, crop_yield_df, "adm_id", "Year")
countries_summary_df = pd.DataFrame.from_dict(countries_summary, columns=column_names,
                                              orient="index")
print(countries_summary_df.head(30).to_string())
variables = ["Yield", "Area", "Production"]

rename_cols = {
  "Yield" : "yield",
  "Area" : "harvest_area",
  "Production" : "production",
  "Crop" : "crop_name",
  "Year" : "harvest_year",
}

sel_cols = ["crop_name", "adm_id", "harvest_year", "production", "harvest_area", "yield"]
final_stats = None

for cr in selected_crops:
  print("\n")
  print("Crop:", cr)
  crop_stats = crop_stats_df[crop_stats_df["Crop"] == cr].copy()
  crop_stats = crop_stats.rename(columns=rename_cols)
  crop_stats = crop_stats.drop(columns=[c for c in crop_stats.columns if c not in sel_cols])
  crop_stats = crop_stats.dropna(axis=0)

  if (final_stats is None):
    final_stats = crop_stats
  else:
    final_stats = pd.concat([final_stats, crop_stats], axis=0)

  print(crop_stats.head(10).to_string())
final_stats["country_code"] = final_stats["adm_id"].str[:2]
# country_code is added as the last column, reorder columns
col_order = ["crop_name", "country_code", "adm_id", "harvest_year", "harvest_area", "yield", "production"]
final_stats = final_stats[col_order]
print(final_stats.head(5).to_string())
final_stats.to_csv(data_path + "/" + "YIELD_COUNTY_US.csv", index=False)
