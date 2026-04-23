@echo off
@echo UPDATE recipe and machine files (CSV-Files) 2025-05-14

@Rem folder pathes
set dstdir1=D:\User
set recipeDir=%~dp0

@echo recipe folder: %recipeDir%
@echo tool exe: %dstdir1%\CsvConverter\CsvConverter

%dstdir1%\CsvConverter\CsvConverter %recipeDir%recipe.csv %recipeDir%tmp_recipe.csv
%dstdir1%\CsvConverter\CsvConverter %recipeDir%layer.csv %recipeDir%tmp_layer.csv
%dstdir1%\CsvConverter\CsvConverter %recipeDir%machine.csv %recipeDir%tmp_machine.csv
%dstdir1%\CsvConverter\CsvConverter %recipeDir%Trend.CSV %recipeDir%tmp_Trend.CSV
%dstdir1%\CsvConverter\CsvConverter %recipeDir%SEQ_INIT.CSV %recipeDir%tmp_SEQ.CSV
%dstdir1%\CsvConverter\CsvConverter %recipeDir%SEQ.CSV %recipeDir%tmp_SEQ.CSV
%dstdir1%\CsvConverter\CsvConverter %recipeDir%HandlingSequence.CSV %recipeDir%tmp_HandlingSequence.CSV
%dstdir1%\CsvConverter\CsvConverter %recipeDir%Production.CSV %recipeDir%tmp_Production.CSV

@REM alle bereits bestehenden SEQ_xxx.CSV anpassen:
for %%s in ("%recipeDir%SEQ_*.CSV") do %dstdir1%\CsvConverter\CsvConverter "%%s" %recipeDir%tmp_SEQ.CSV
@pause
