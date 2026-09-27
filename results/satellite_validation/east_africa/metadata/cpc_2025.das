Attributes {
    lat {
        Float32 actual_range 89.75, -89.75;
        String long_name "Latitude";
        String units "degrees_north";
        String axis "Y";
        String standard_name "latitude";
        String coordinate_defines "center";
    }
    lon {
        String long_name "Longitude";
        String units "degrees_east";
        String axis "X";
        String standard_name "longitude";
        Float32 actual_range 0.25, 359.75;
        String coordinate_defines "center";
    }
    time {
        String long_name "Time";
        String axis "T";
        String standard_name "time";
        String coordinate_defines "start";
        String delta_t "0000-00-01 00:00:00";
        String avg_period "0000-00-01 00:00:00";
        String units "hours since 1900-01-01 00:00:00";
        Float64 actual_range 1095744.0, 1104480.0;
        UInt32 _ChunkSizes 1;
    }
    precip {
        Float32 missing_value -9.96921E36;
        String units "mm";
        String var_desc "Precipitation";
        String level_desc "Surface";
        String statistic "Total";
        String parent_stat "Other";
        String long_name "Daily total of precipitation";
        String cell_methods "time: sum";
        Float32 valid_range 0.0, 1000.0;
        String avg_period "0000-00-01 00:00:00";
        Float32 actual_range 0.0, 776.75;
        String dataset "CPC Global Precipitation";
        UInt32 _ChunkSizes 1, 360, 720;
    }
    NC_GLOBAL {
        String Conventions "CF-1.0";
        String version "V1.0";
        String title "CPC GLOBAL PRCP V1.0 RT";
        String References "https://www.psl.noaa.gov/data/gridded/data.cpc.globalprecip.html";
        String dataset_title "CPC GLOBAL PRCP V1.0";
        String Source "ftp://ftp.cpc.ncep.noaa.gov/precip/CPC_UNI_PRCP/";
        String _NCProperties "version=2,netcdf=4.7.0,hdf5=1.10.5,";
        String history "Updated 2026-01-02 23:30:30";
    }
    DODS_EXTRA {
        String Unlimited_Dimension "time";
    }
}
