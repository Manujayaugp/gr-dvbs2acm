#pragma once

// DLL export/import macros for Windows compatibility
#ifdef _WIN32
  #ifdef dvbs2acm_EXPORTS
    #define DVBS2ACM_API __declspec(dllexport)
  #else
    #define DVBS2ACM_API __declspec(dllimport)
  #endif
#else
  #define DVBS2ACM_API __attribute__((visibility("default")))
#endif
