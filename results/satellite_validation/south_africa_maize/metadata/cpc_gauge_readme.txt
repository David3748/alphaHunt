              CPC Gauge-Based Analysis of Global Daily Precipitation 

1)  Product Name 

    CPC Unified Gauge-Based Analysis of Global Daily Precipitation 

2)  Background 

    This data set is the first product of the CPC Unified Precipitation 
Project that are underway at NOAA Climate Prediction Center (CPC). The primary  
goal of the project is to create a suite of unified precipitation products with 
consistent quantity and improved quality by combining all information sources
available at CPC and by taking advantage of the optimal interpolation (OI) 
objective analysis technique.  

3)  Coverage and Resolution 

    Space :   0.5deg lat / lon;  Global Land 
    Time  :   Daily;             Jan.1, 1979 - Present 

    1979 - 2005    : Rereospective Version (30K+ gauges) 
    2006 - Present : Real-time version   (~17K gauges) 

4)  Cautions and Alerts 

    *  Quality of the gauge-based analysis is poor over tropical Africa and
       Antartica;  

    *  Elsewhere, the analysis quality varies with the gauge network density.
       Users are encouraged to take advantage of the network density 
       information included in the data file; 

    *  Definition of the ending time for the daily accumulation differs from 
       country to country.  There might be discontinuity in the analyzed 
       fields across the national boundaies; 

5)  Definition of the End of Day (EOD) time 

    The EOD time differs from country to country. A GrADS formatted binary 
data file is included to indicate the definition of the EOD on each 0.5deg 
lat/lon grid box.  However, the EOD included in the file is based on very 
limitted and UNCHECKED information that we have been able to get.  Users 
are encouraged to double-check the reliability of the information if accuracy
of the EOD is critical to their applications.  Please report us any of your 
findings regarding the error or suspicions with the EOD. 

6)  Data Files   
 
    *  Document files are in sub-directory /docu 
    *  Retrospective version data files are in sub-directory /v1.0 
    *  Real-time version data files are in sub-directory /RT 

6.1)  End of Day (EOD) time file 

      PRCP_CU_GAUGE_V1.0GLB_0.50deg_EOD.lnx       -----   Binary data file 
      PRCP_CU_GAUGE_V1.0GLB_0.50deg_EOD.lnx.ctl   -----   GrADS control file 

6.2)  Analysis data files 

      *  Data files for each calendar year are stored in a sub-directory 'yyyy' 
         where 'yyyy' is a 4-digit integer; 

      *  Under each yearly sub-directory, analysis data for each day are  
         included in a single GrADS formatted data file named: 

             PRCP_CU_GAUGE_V1.0GLB_0.50deg.lnx.yyyymmdd   ; or 
             PRCP_CU_GAUGE_V1.0GLB_0.50deg.lnx.yyyymmddRT 

         where 'yyyymmdd' denotes the target year, month, and date. 

         'RT' at the end of the file name indicates that the analysis for the 
         day is based on the real-time station data and the analysis will be 
         reprocessed later on when enhanced station data are available. 

      *  In each daily file, there are two global fields: 
            analyzed field of daily precipitation in 0.1mm; and 
            number of gauges available in each 0.5deg lat/lon grid box. 

      *  The global field starts from the 0.5deg lat/lon grid box centering 
         at (0.25E; 89.75S), going from west to east first, and then south to 
         north. 

      *  Both the global fields are written as 4-byte real numbers in 
         LITTLE_ENDIAN byte order.  You may need to swap the byte order if
         you are working with a big_endian machine (such as an SGI). 

      *  The following GrADS control file for the daily data is available: 
            PRCP_CU_GAUGE_V1.0GLB_0.50deg.lnx.ctl 

6.3) Quality Check Report (QCR) file 

     An ASCII text file is included in each yearly sub-directory to list  
     the known problems / limitations with the analysis.  The file is named: 

         PRCP_CU_GAUGE_V1.0GLB_0.50deg_QCR.txt.yyyy

     These problems / limitations are discovered through manual checks by the 
     data producers.  
 
7)  References: 

    (Interpolation algorithm) 
    Xie_et_al_2007_JHM_EAG.pdf 
    Xie, P., A. Yatagai, M. Chen, T. Hayasaka, Y. Fukushima, C. Liu, and
    S. Yang (2007), A gauge-based analysis of daily precipitation over East
    Asia, J. Hydrometeorol., 8, 607. 626. 

    (Gauge Algorithm Evaluation) 
    Chen_et_al_2008_JGR_Gauge_Algo.pdf  
    Chen, M., W. Shi, P. Xie, V. B. S. Silva, V E. Kousky, R. Wayne Higgins,
    and J. E. Janowiak (2008), Assessing objective techniques for gauge-based
    analyses of global daily precipitation, J. Geophys. Res., 113, D04110,
    doi:10.1029/2007JD009132.

    (Construction of the Daily Gauge Analysis) 
    Chen_et_al_2008_Daily_Gauge_Anal.pdf 
    Chen, M., P. Xie, and Co-authors (2008), CPC Unified Gauge-based Analysis
    of Global Daily Precipiation, Western Pacific Geophysics Meeting, Cairns,
    Australia, 29 July - 1 August, 2008.

8)  Contacts 

Dr. Pingping Xie 
Climate Prediction Center 
5200 Auth Road, #605 
Camp Springs, MD 20746 
USA 
E-Mail:  Pingping.Xie@noaa.gov 
Tel.     (301)763-8000, #7572 

Dr. Wei Shi 
Climate Prediction Center 
5200 Auth Road, #605
Camp Springs, MD 20746
USA
E-Mail:  Wei.Shi@noaa.gov
Tel.     (301)763-8000, #7545 

Dr. Mingyue Chen 
Climate Prediction Center
5200 Auth Road, #605
Camp Springs, MD 20746
USA
E-Mail:  Mingyue.Chen@noaa.gov 
Tel.     (301)763-8000, #7506  

