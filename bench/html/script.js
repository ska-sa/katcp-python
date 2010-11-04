
$.jqplot.config.enablePlugins = true;

$(document).ready(function() {
    $.getJSON("out.json", function(data) {
        for (var index in data) {
            var elem = data[index];
            var toplot = [];
            var series = [];
            for (i = 0; i < elem.length; ++i) {
                toplot.push(elem[i].result);
                series.push({label: elem[i].lib + " " + elem[i].interpreter,
                             renderer: $.jqplot.BarRenderer});
            }
            $.jqplot(index, toplot,
                     {legend: {show: true},
                      title: index,
                      series: series,
                      axes: {
                          xaxis: {
                              label: "No of clients",
                          },
                          yaxis: {
                              label: "Req/0.2s",
                          }
                      }
                     });
        }
    });
});