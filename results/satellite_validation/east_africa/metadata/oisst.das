Attributes {
    time {
        String long_name "Time";
        String units "days since 1800-01-01 00:00:00";
        String delta_t "0000-01-00 00:00:00";
        String avg_period "0000-01-00 00:00:00";
        String axis "T";
        String calendar "gregorian";
        Float64 actual_range 66352.0, 82757.0;
        UInt32 _ChunkSizes 512;
    }
    lat {
        String long_name "Latitude";
        String standard_name "latitude";
        String units "degrees_north";
        Float32 actual_range -89.875, 89.875;
        String axis "Y";
    }
    lon {
        String long_name "Longitude";
        String standard_name "longitude";
        String units "degrees_east";
        Float32 actual_range 0.125, 359.875;
        String axis "X";
    }
    sst {
        String long_name "Monthly Mean of Sea Surface Temperature";
        String units "degC";
        Float32 valid_range -3.0, 45.0;
        Float32 missing_value -9.96921E36;
        Float32 precision 2.0;
        String dataset "NOAA High-resolution Blended Analysis";
        String var_desc "Sea Surface Temperature";
        String level_desc "Surface";
        String statistic "Monthly Mean";
        String parent_stat "Individual Observations";
        Float32 actual_range -1.8, 32.14;
        String standard_name "sea_surface_temperature";
        UInt32 _ChunkSizes 1, 720, 1440;
    }
    NC_GLOBAL {
        String Conventions "CF-1.5";
        String title "NOAA/NCEI 1/4 Degree Daily Optimum Interpolation Sea Surface Temperature (OISST) Analysis, Version 2.1";
        String institution "NOAA/National Centers for Environmental Information";
        String source "NOAA/NCEI https://www.ncei.noaa.gov/data/sea-surface-temperature-optimum-interpolation/v2.1/access/avhrr/";
        String References "https://www.psl.noaa.gov/data/gridded/data.noaa.oisst.v2.highres.html";
        String dataset_title "NOAA Daily Optimum Interpolation Sea Surface Temperature";
        String version "Version 2.1";
        String comment "Reynolds, et al.(2007) Daily High-Resolution-Blended Analyses for Sea Surface Temperature (available at https://doi.org/10.1175/2007JCLI1824.1). Banzon, et al.(2016) A long-term record of blended satellite and in situ sea-surface temperature for climate monitoring, modeling and environmental studies (available at https://doi.org/10.5194/essd-8-165-2016). Huang, B., C. Liu, V. Banzon, E. Freeman, G. Graham, B. Hankins, T. Smith, and H.-M. Zhang, 2021: Improvements of the Daily Optimum Interpolation Sea Surface Temperature (DOISST) Version 2.1 (available at https://doi.org/10.1175/JCLI-D-20-0166.1). Anomaly is based on 1971-2000 OI.v2 SST. Satellite data: multi-satellite sensor (AVHRR+VIIRS) data. Ice data: NCEP Ice and GSFC Ice. Data less than 15 days old may be subject to revision.";
        String _NCProperties "version=2,netcdf=4.7.0,hdf5=1.10.5,";
    }
    DODS_EXTRA {
        String Unlimited_Dimension "time";
    }
}
